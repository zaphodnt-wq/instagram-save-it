package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	igWebAppID    = "936619743392459"
	igMobileAppID = "124024574287414"
	webUserAgent  = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
	mobUserAgent  = "Instagram 290.0.0.0.31 (iPad13,8; iOS 16_6; en_US; en-US; scale=2.00; 2048x2732; 497648127) AppleWebKit/420+"
)

// IGSession stores the minimal cookies needed to authenticate with Instagram's v1 API.
type IGSession struct {
	Username  string `json:"username"`
	SessionID string `json:"sessionid"`
	CSRFToken string `json:"csrftoken"`
	DSUserID  string `json:"ds_user_id"`
}

// Structures matching Instagram's `GET /api/v1/feed/saved/posts/` JSON response.
type savedFeedResponse struct {
	Status        string      `json:"status"`
	Items         []savedItem `json:"items"`
	MoreAvailable bool        `json:"more_available"`
	NextMaxID     any         `json:"next_max_id"`
}

type savedItem struct {
	Media *igMedia `json:"media"`
}

type igMedia struct {
	PK            any             `json:"pk"`
	ID            string          `json:"id"`
	Code          string          `json:"code"`
	TakenAt       int64           `json:"taken_at"`
	MediaType     int             `json:"media_type"` // 1 = Image, 2 = Video, 8 = Carousel
	ImageVersions *imageVersions2 `json:"image_versions2"`
	VideoVersions []videoVersion  `json:"video_versions"`
	CarouselMedia []igMedia       `json:"carousel_media"`
}

type imageVersions2 struct {
	Candidates []mediaCandidate `json:"candidates"`
}

type mediaCandidate struct {
	URL string `json:"url"`
}

type videoVersion struct {
	URL string `json:"url"`
}

func sessionFilePath(username string) string {
	return filepath.Join(".sessions", fmt.Sprintf("go-session-%s.json", username))
}

// SaveSessionWithCookie verifies a browser sessionid cookie against Instagram and saves it to disk.
func SaveSessionWithCookie(username, rawSessionID string) (*IGSession, error) {
	decoded, err := url.QueryUnescape(rawSessionID)
	if err != nil {
		decoded = rawSessionID
	}
	// Strip any accidental spaces or quotes from copy-pasting
	cleanID := strings.Join(strings.Fields(decoded), "")
	cleanID = strings.Trim(cleanID, "\"'")

	dsUserID := ""
	if parts := strings.SplitN(cleanID, ":", 2); len(parts) > 0 {
		dsUserID = parts[0]
	}

	sess := &IGSession{
		Username:  username,
		SessionID: cleanID,
		CSRFToken: "missing",
		DSUserID:  dsUserID,
	}

	// Verify session against the saved posts endpoint and capture a fresh csrftoken cookie
	if _, err := fetchSavedPage(sess, ""); err != nil {
		return nil, fmt.Errorf("could not verify login with provided sessionid: %w", err)
	}

	if err := writeSessionFile(sess); err != nil {
		return nil, err
	}
	fmt.Printf("Verified login for @%s and saved session to %s\n", username, sessionFilePath(username))
	return sess, nil
}

func writeSessionFile(sess *IGSession) error {
	if err := os.MkdirAll(".sessions", 0700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(sess, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(sessionFilePath(sess.Username), data, 0600)
}

func LoadSessionFile(username string) (*IGSession, error) {
	path := sessionFilePath(username)
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("session file %s not found (run with --login-only --sessionid first): %w", path, err)
	}
	var sess IGSession
	if err := json.Unmarshal(data, &sess); err != nil {
		return nil, err
	}
	fmt.Printf("Loaded session from %s.\n", path)
	return &sess, nil
}

// fetchSavedPage queries one page of `GET /api/v1/feed/saved/posts/`.
func fetchSavedPage(sess *IGSession, maxID string) (*savedFeedResponse, error) {
	endpoint := "https://www.instagram.com/api/v1/feed/saved/posts/?include_igtv_preview=false"
	if maxID != "" {
		endpoint += "&max_id=" + url.QueryEscape(maxID)
	}

	req, err := http.NewRequest("GET", endpoint, nil)
	if err != nil {
		return nil, err
	}
	setWebHeaders(req, sess, fmt.Sprintf("https://www.instagram.com/%s/saved/all-posts/", sess.Username))

	client := &http.Client{Timeout: 20 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	// Update csrftoken if Instagram sent a fresh one
	for _, c := range resp.Cookies() {
		if c.Name == "csrftoken" && c.Value != "" {
			sess.CSRFToken = c.Value
		}
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d from saved posts endpoint", resp.StatusCode)
	}

	var parsed savedFeedResponse
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		return nil, err
	}
	if parsed.Status != "ok" {
		return nil, fmt.Errorf("unexpected API status: %s", parsed.Status)
	}
	return &parsed, nil
}

func setWebHeaders(req *http.Request, sess *IGSession, referer string) {
	req.Header.Set("User-Agent", webUserAgent)
	req.Header.Set("X-IG-App-ID", igWebAppID)
	req.Header.Set("X-Instagram-AJAX", "1")
	req.Header.Set("X-Requested-With", "XMLHttpRequest")
	req.Header.Set("Referer", referer)
	if sess.CSRFToken != "" {
		req.Header.Set("X-CSRFToken", sess.CSRFToken)
	}
	cookieParts := []string{
		fmt.Sprintf("sessionid=%s", sess.SessionID),
		fmt.Sprintf("ds_user_id=%s", sess.DSUserID),
	}
	if sess.CSRFToken != "" {
		cookieParts = append(cookieParts, fmt.Sprintf("csrftoken=%s", sess.CSRFToken))
	}
	req.Header.Set("Cookie", strings.Join(cookieParts, "; "))
}

// DownloadSavedPosts fetches saved posts and downloads clean .mp4 / .jpg files into stagingDir.
func DownloadSavedPosts(sess *IGSession, stagingDir string, maxCount int, fastUpdate bool, archived map[string]bool) ([]string, error) {
	if err := os.MkdirAll(stagingDir, 0755); err != nil {
		return nil, err
	}

	fmt.Printf("Fetching saved posts for @%s (max_count=%d)...\n", sess.Username, maxCount)
	var newlyDownloaded []string
	maxID := ""

	for {
		page, err := fetchSavedPage(sess, maxID)
		if err != nil {
			return newlyDownloaded, err
		}
		_ = writeSessionFile(sess)

		stop := false
		for _, item := range page.Items {
			media := item.Media
			if media == nil || media.Code == "" {
				continue
			}

			if archived[media.Code] {
				if fastUpdate {
					fmt.Printf("Reached already-archived post %s; stopping (--fast-update).\n", media.Code)
					stop = true
					break
				}
				fmt.Printf("Skipping already-archived post %s.\n", media.Code)
				continue
			}

			fmt.Printf("Downloading saved post %s...\n", media.Code)
			if err := downloadMediaItem(media, stagingDir); err != nil {
				return newlyDownloaded, fmt.Errorf("failed downloading %s: %w", media.Code, err)
			}
			newlyDownloaded = append(newlyDownloaded, media.Code)

			if maxCount > 0 && len(newlyDownloaded) >= maxCount {
				fmt.Printf("Reached max_count=%d posts for this run.\n", maxCount)
				stop = true
				break
			}
		}

		if stop || !page.MoreAvailable || page.NextMaxID == nil {
			break
		}
		maxID = fmt.Sprintf("%v", page.NextMaxID)
		if maxID == "" {
			break
		}
	}

	fmt.Printf("Downloaded %d new saved post(s) into %s.\n", len(newlyDownloaded), stagingDir)
	return newlyDownloaded, nil
}

func downloadMediaItem(media *igMedia, stagingDir string) error {
	ts := time.Unix(media.TakenAt, 0).UTC().Format("2006-01-02_15-04-05")
	baseName := fmt.Sprintf("%s_UTC_%s", ts, media.Code)

	// Carousel post (multiple images/videos)
	if len(media.CarouselMedia) > 0 {
		for idx, child := range media.CarouselMedia {
			urlStr, ext := extractMediaURL(&child)
			if urlStr == "" {
				continue
			}
			dest := filepath.Join(stagingDir, fmt.Sprintf("%s_%d%s", baseName, idx+1, ext))
			if err := downloadFile(urlStr, dest); err != nil {
				return err
			}
		}
		return nil
	}

	// Single video or image post
	urlStr, ext := extractMediaURL(media)
	if urlStr == "" {
		return fmt.Errorf("no media URL found for post %s", media.Code)
	}
	dest := filepath.Join(stagingDir, baseName+ext)
	return downloadFile(urlStr, dest)
}

func extractMediaURL(m *igMedia) (string, string) {
	if len(m.VideoVersions) > 0 && m.VideoVersions[0].URL != "" {
		return m.VideoVersions[0].URL, ".mp4"
	}
	if m.ImageVersions != nil && len(m.ImageVersions.Candidates) > 0 {
		return m.ImageVersions.Candidates[0].URL, ".jpg"
	}
	return "", ""
}

func downloadFile(urlStr, destPath string) error {
	if _, err := os.Stat(destPath); err == nil {
		fmt.Printf("  %s exists\n", destPath)
		return nil
	}

	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Get(urlStr)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d downloading %s", resp.StatusCode, urlStr)
	}

	out, err := os.Create(destPath)
	if err != nil {
		return err
	}
	defer out.Close()

	if _, err := io.Copy(out, resp.Body); err != nil {
		return err
	}
	fmt.Printf("  saved %s\n", destPath)
	return nil
}

// UnsavePosts removes verified posts from the user's Instagram Saved collection.
func UnsavePosts(sess *IGSession, shortcodes []string) []string {
	if len(shortcodes) == 0 {
		return nil
	}

	// Refresh CSRF token first
	_, _ = fetchSavedPage(sess, "")
	_ = writeSessionFile(sess)

	tokenPayload, _ := json.Marshal(map[string]string{
		"ds_user_id": sess.DSUserID,
		"sessionid":  sess.SessionID,
	})
	igtToken := base64.StdEncoding.EncodeToString(tokenPayload)
	client := &http.Client{Timeout: 15 * time.Second}

	var unsaved []string
	for _, code := range shortcodes {
		mediaID := shortcodeToMediaID(code)
		if unsaveViaWeb(client, sess, code, mediaID) || unsaveViaMobile(client, sess, igtToken, mediaID) {
			fmt.Printf("Unsaved post %s from Instagram.\n", code)
			unsaved = append(unsaved, code)
		} else {
			fmt.Fprintf(os.Stderr, "Warning: could not unsave post %s.\n", code)
		}
	}
	return unsaved
}

func unsaveViaWeb(client *http.Client, sess *IGSession, shortcode, mediaID string) bool {
	endpoint := fmt.Sprintf("https://www.instagram.com/web/save/%s/unsave/", mediaID)
	req, err := http.NewRequest("POST", endpoint, nil)
	if err != nil {
		return false
	}
	setWebHeaders(req, sess, fmt.Sprintf("https://www.instagram.com/p/%s/", shortcode))
	req.Header.Set("Origin", "https://www.instagram.com")

	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return checkStatusOK(resp)
}

func unsaveViaMobile(client *http.Client, sess *IGSession, igtToken, mediaID string) bool {
	endpoint := fmt.Sprintf("https://i.instagram.com/api/v1/media/%s/unsave/", mediaID)
	form := url.Values{}
	form.Set("media_id", mediaID)
	form.Set("module_name", "feed_contextual_saved_collections")

	req, err := http.NewRequest("POST", endpoint, strings.NewReader(form.Encode()))
	if err != nil {
		return false
	}
	req.Header.Set("User-Agent", mobUserAgent)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("X-IG-App-ID", igMobileAppID)
	req.Header.Set("Authorization", "Bearer IGT:2:"+igtToken)
	req.Header.Set("ig-intended-user-id", sess.DSUserID)
	req.Header.Set("ig-u-ds-user-id", sess.DSUserID)

	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return checkStatusOK(resp)
}

func checkStatusOK(resp *http.Response) bool {
	if resp.StatusCode != http.StatusOK {
		return false
	}
	var res struct {
		Status string `json:"status"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&res); err != nil {
		return false
	}
	return res.Status == "ok"
}

// shortcodeToMediaID converts an 11-char Instagram base64url shortcode into its decimal media ID.
func shortcodeToMediaID(code string) string {
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
	var id uint64
	for i := 0; i < len(code); i++ {
		idx := strings.IndexByte(alphabet, code[i])
		if idx >= 0 {
			id = (id << 6) | uint64(idx)
		}
	}
	return fmt.Sprintf("%d", id)
}
