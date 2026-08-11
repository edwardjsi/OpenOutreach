# Questions — Substack CommentCrafter

**Date**: 2026-07-17
**Status**: Awaiting answers before building

---

## Core workflow

### 1. Discovery
How do I find the writers/posts to engage with? You mentioned "people in specific subjects" — do you give me a list of Substack publications to watch, or do I search by topic/category, or both?

### 2. Comment generation
When I scan a post and draft a comment, how many do you want to see per email? One best candidate, or a batch of 3-5 to choose from?

### 3. Email approval flow
- You read the post snippet + my drafted comment in the email
- You reply "yes" / edit the text and reply / ignore
- I post on approval

What happens if you don't reply within, say, 24 hours? Skip the post? Re-send the digest?

### 4. Sending email to you
SMTP? SendGrid / SES / Resend API? Do you have a preferred email service, or should I set up something lightweight like `smtplib` with your Gmail app password?

---

## Platform constraints

### 5. Substack comments
Do you have a Substack account I log into (browser automation, like the LinkedIn approach), or are comments posted through Substack's API? (Substack likely has no public comment API — so Playwright again, but selectors and navigation will differ from LinkedIn.)

### 6. Logged-in session
I'll need your Substack credentials or a session cookie. How do you want to handle that — manual login, cookie paste, env vars?

---

## Volume & timing

### 7. Daily volume
How many comments per day are you aiming for? 3-5? 10-20?

### 8. Timing
Do you want the daemon to check for new posts hourly / twice a day / on a schedule you control?

---

## What to watch

### 9. Publication list
At launch, do you have a list of Substacks you want to monitor, or do you want me to discover them from a topic description (like OpenOutreach does with search queries)?

### 10. Your own Substack
Are you a writer yourself? If so, the comment persona should reflect your actual voice/background rather than a generic AI persona.

---

## Tech stack

### 11. LLM
Same question as OpenOutreach — you bring your own API key (OpenAI/Anthropic/etc.)?

### 12. Storage
Lightweight SQLite again, same as the initial CommentCrafter build, or something else?

### 13. Deployment
CLI tool on your machine, or do you want a Docker container that runs persistently?
