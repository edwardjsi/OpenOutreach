# Integrate OpenOutreach Session with LinkedIn Marketing Skills
**Date:** September 10, 2026

This plan details how we will build a custom script to utilize your active OpenOutreach browser session for reading influencers' posts and generating comments using the newly imbibed `linkedin-marketing` skills, and sending the results to your Telegram.

## Goal Description

Create a script that reads a list of influencer LinkedIn profile URLs, automatically navigates to their recent activity using your active OpenOutreach browser session, extracts their latest post, drafts engaging comments using the existing OpenAI integration, and sends the drafted comments directly to your Telegram via the Bot API.

## Proposed Changes

### 1. New Django Management Command
#### [NEW] `linkedin/management/commands/draft_comments.py`
We will create a management command (runnable via `python manage.py draft_comments <profile_urls>`) that performs the following steps:
1.  **Initialize Context**: Load the active `LinkedInProfile`, `SiteConfig` (for Telegram credentials), and the configured OpenAI API key from the database.
2.  **Fetch Latest Post**: For each provided profile URL:
    *   Initialize the Playwright `AccountSession`.
    *   Navigate to the influencer's recent activity page (`/in/<username>/recent-activity/all/`).
    *   Extract the URL and text of their most recent post under the radar.
3.  **Draft Comments**: Use the OpenAI client and the exact system prompts from `.agents/plugins/linkedin-marketing/skills/linkedin-comment-drafter/references/comment-templates.md` to generate 2-3 engaging comment variants (e.g., "Missing Piece", "Answer the Closing Question").
4.  **Send to Telegram**: Format the post URL and the generated comment variants into a clean message and send it to you using `linkedin.notifications._send_telegram`.

## User Review Required

Please review the proposed approach above. 

There are no more open questions since you clarified to use Telegram, the existing OpenAI config, and the profile URL format. Once you approve this plan, I will begin execution by writing the management command.

## Verification Plan

### Manual Verification
1. We will run the command `python manage.py draft_comments https://www.linkedin.com/in/ankush-singla/` as a dry-run test.
2. I will verify that the script successfully navigates to the profile, fetches the latest post, generates the comments, and that you receive the Telegram notification successfully without any errors or security flags from LinkedIn.
