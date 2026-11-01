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

// VerifyMedia runs `rclone check --one-way` to confirm every staged file matches Google Drive.
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
