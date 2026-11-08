package main

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
)

const (
	DefaultStagingDir = "staging"
	DefaultRemoteDest = "gdrive:rclone/save"
)

// TransferMedia runs `rclone copy` to upload clean media files from sourceDir to remoteDest.
// Output is streamed directly to the terminal so you can watch live upload progress.
func TransferMedia(sourceDir, remoteDest string, dryRun bool) error {
	args := []string{
		"copy",
		sourceDir,
		remoteDest,
		"--include", "*.{jpg,jpeg,png,mp4}",
		"--transfers", "4",
		"--checkers", "8",
		"--progress",
		"-v",
	}
	if dryRun {
		args = append(args, "--dry-run")
	}

	fmt.Printf("Running: rclone %s\n", strings.Join(args, " "))
	cmd := exec.Command("rclone", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("rclone copy failed: %w", err)
	}
	return nil
}

// VerifyMedia runs `rclone check --one-way` to confirm every staged file matches the destination.
func VerifyMedia(sourceDir, remoteDest string) error {
	args := []string{
		"check",
		sourceDir,
		remoteDest,
		"--include", "*.{jpg,jpeg,png,mp4}",
		"--one-way",
	}

	fmt.Printf("Verifying: rclone %s\n", strings.Join(args, " "))
	cmd := exec.Command("rclone", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("rclone check failed: %w", err)
	}
	fmt.Println("rclone check passed!")
	return nil
}

// MoveRemoteToLocal runs `rclone move` to move media files from remoteSrc (e.g. gdrive:rclone/save)
// into localDest (e.g. Y:\eDrive\instagram1\instaGramDL9), deleting each remote file only after
// rclone verifies the local copy succeeded.
func MoveRemoteToLocal(remoteSrc, localDest string, dryRun bool) error {
	if !dryRun {
		if err := os.MkdirAll(localDest, 0755); err != nil {
			return fmt.Errorf("could not create local directory %s: %w", localDest, err)
		}
	}

	args := []string{
		"move",
		remoteSrc,
		localDest,
		"--include", "*.{jpg,jpeg,png,mp4}",
		"--transfers", "4",
		"--checkers", "8",
		"--progress",
		"-v",
	}
	if dryRun {
		args = append(args, "--dry-run")
	}

	fmt.Printf("Moving from %s to local directory %s: rclone %s\n", remoteSrc, localDest, strings.Join(args, " "))
	cmd := exec.Command("rclone", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("rclone move to local directory failed: %w", err)
	}
	fmt.Printf("Successfully moved files from %s to %s.\n", remoteSrc, localDest)
	return nil
}
