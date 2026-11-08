package main

import (
	"bufio"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const (
	ArchiveLogFile = ".archived_shortcodes.txt"
	UnsavedLogFile = ".unsaved_shortcodes.txt"
)

func loadShortcodeSet(filename string) map[string]bool {
	set := make(map[string]bool)
	f, err := os.Open(filename)
	if err != nil {
		return set
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line != "" {
			set[line] = true
		}
	}
	return set
}

func appendShortcodes(filename string, codes []string) error {
	if len(codes) == 0 {
		return nil
	}
	f, err := os.OpenFile(filename, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return err
	}
	defer f.Close()

	for _, c := range codes {
		if _, err := fmt.Fprintln(f, c); err != nil {
			return err
		}
	}
	return nil
}

func hasStagedFiles(dir string) bool {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return false
	}
	return len(entries) > 0
}

func cleanStaging(dir string) error {
	entries, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	for _, entry := range entries {
		if err := os.RemoveAll(filepath.Join(dir, entry.Name())); err != nil {
			return err
		}
	}
	fmt.Printf("Cleaned local staging directory: %s\n", dir)
	return nil
}

func pendingUnsaveList(newCodes []string) []string {
	archived := loadShortcodeSet(ArchiveLogFile)
	unsaved := loadShortcodeSet(UnsavedLogFile)

	seen := make(map[string]bool)
	var result []string

	for _, c := range newCodes {
		if !unsaved[c] && !seen[c] {
			seen[c] = true
			result = append(result, c)
		}
	}
	for c := range archived {
		if !unsaved[c] && !seen[c] {
			seen[c] = true
			result = append(result, c)
		}
	}
	return result
}

func main() {
	username := flag.String("u", "", "Your Instagram username (required unless running --move-to-local standalone)")
	flag.StringVar(username, "username", "", "Your Instagram username (required unless running --move-to-local standalone)")
	loginOnly := flag.Bool("login-only", false, "Verify sessionid cookie, save session file, and exit")
	sessionID := flag.String("sessionid", "", "Instagram 'sessionid' cookie from a logged-in browser")
	maxCount := flag.Int("n", 10, "Maximum number of saved posts to download per run (0 for unlimited)")
	flag.IntVar(maxCount, "max-count", 10, "Maximum number of saved posts to download per run (0 for unlimited)")
	workers := flag.Int("w", 4, "Number of concurrent download workers (default: 4)")
	flag.IntVar(workers, "workers", 4, "Number of concurrent download workers (default: 4)")
	noFastUpdate := flag.Bool("no-fast-update", false, "Continue scanning past already-archived posts")
	dryRun := flag.Bool("dry-run", false, "Run rclone in --dry-run mode without modifying remote/local files or unsaving")
	clean := flag.Bool("clean", false, "Delete local staging files after rclone check verifies upload integrity")
	unsave := flag.Bool("unsave", false, "Remove posts from your Instagram Saved list after destination verification")
	resetArchive := flag.Bool("reset-archive", false, "Clear .archived_shortcodes.txt before downloading")
	remote := flag.String("remote", DefaultRemoteDest, "rclone destination (default: gdrive:rclone/save)")
	localDir := flag.String("local-dir", "", "Save directly from ./staging to a local directory (e.g. Y:\\eDrive\\instagram1\\instaGramDL9) instead of Google Drive")
	flag.StringVar(localDir, "d", "", "Save directly from ./staging to a local directory instead of Google Drive")
	moveToLocal := flag.String("move-to-local", "", "Move saved files from --remote (gdrive:rclone/save) to a local directory (e.g. Y:\\eDrive\\instagram1\\instaGramDL9) via rclone move")
	flag.StringVar(moveToLocal, "m", "", "Move saved files from --remote (gdrive:rclone/save) to a local directory via rclone move")

	flag.Parse()

	// Standalone mode: if --move-to-local is specified without -u, drain --remote into the local directory and exit.
	if *username == "" && *moveToLocal != "" {
		if err := MoveRemoteToLocal(*remote, *moveToLocal, *dryRun); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}

	if *username == "" {
		fmt.Fprintln(os.Stderr, "Error: -u <instagram_username> is required (or pass --move-to-local <PATH> standalone).")
		flag.Usage()
		os.Exit(1)
	}

	destTarget := *remote
	if *localDir != "" {
		destTarget = *localDir
		if !*dryRun {
			if err := os.MkdirAll(destTarget, 0755); err != nil {
				fmt.Fprintf(os.Stderr, "Error creating local directory %s: %v\n", destTarget, err)
				os.Exit(1)
			}
		}
	}

	// Step 1: Login / Session Setup
	var sess *IGSession
	var err error
	if *loginOnly || *sessionID != "" {
		rawCookie := *sessionID
		if len(flag.Args()) > 0 {
			rawCookie += strings.Join(flag.Args(), "")
		}
		if rawCookie == "" {
			fmt.Fprintln(os.Stderr, "Error: please provide --sessionid \"YOUR_SESSIONID_COOKIE\" with --login-only.")
			os.Exit(1)
		}
		sess, err = SaveSessionWithCookie(*username, rawCookie)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Login failed: %v\n", err)
			os.Exit(1)
		}
		if *loginOnly {
			return
		}
	} else {
		sess, err = LoadSessionFile(*username)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}

	if *resetArchive {
		_ = os.Remove(ArchiveLogFile)
		fmt.Printf("Cleared archive history (%s).\n", ArchiveLogFile)
	}

	// Step 2: Download Saved Posts to ./staging (in parallel using -w workers)
	archived := loadShortcodeSet(ArchiveLogFile)
	newCodes, err := DownloadSavedPosts(sess, DefaultStagingDir, *maxCount, *workers, !*noFastUpdate, archived)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error fetching saved posts: %v\n", err)
		os.Exit(1)
	}

	if !hasStagedFiles(DefaultStagingDir) {
		if *unsave && !*dryRun {
			pending := pendingUnsaveList(nil)
			if len(pending) > 0 {
				unsaved := UnsavePosts(sess, pending)
				_ = appendShortcodes(UnsavedLogFile, unsaved)
			}
		}
		if *moveToLocal != "" {
			if err := MoveRemoteToLocal(*remote, *moveToLocal, *dryRun); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
			return
		}
		fmt.Println("No files in staging to transfer.")
		return
	}

	// Step 3: Transfer from ./staging to destination (Google Drive or --local-dir) via rclone copy
	if err := TransferMedia(DefaultStagingDir, destTarget, *dryRun); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	if *dryRun {
		if *moveToLocal != "" {
			if err := MoveRemoteToLocal(*remote, *moveToLocal, true); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
		}
		fmt.Println("Dry-run complete. Staging files and archive log were not modified.")
		return
	}

	// Step 4: Verify Checksums via rclone check --one-way
	if err := VerifyMedia(DefaultStagingDir, destTarget); err != nil {
		fmt.Fprintln(os.Stderr, "Verification failed! Local staging files were kept for safety.")
		os.Exit(1)
	}

	// Step 5: Record Archived Shortcodes & Optionally Unsave + Clean
	if err := appendShortcodes(ArchiveLogFile, newCodes); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: could not update %s: %v\n", ArchiveLogFile, err)
	} else {
		fmt.Printf("Recorded %d post(s) in %s.\n", len(newCodes), ArchiveLogFile)
	}

	if *unsave {
		toUnsave := pendingUnsaveList(newCodes)
		unsaved := UnsavePosts(sess, toUnsave)
		_ = appendShortcodes(UnsavedLogFile, unsaved)
	}

	if *clean {
		if err := cleanStaging(DefaultStagingDir); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: failed to clean staging: %v\n", err)
		}
	}

	// Step 6: Optionally move files from Google Drive (rclone/save) to local directory
	if *moveToLocal != "" {
		if err := MoveRemoteToLocal(*remote, *moveToLocal, *dryRun); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}
}
