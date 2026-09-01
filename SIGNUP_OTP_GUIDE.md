# Signup OTP — Email Verification for New Accounts

## What's New

Signup ab 2-step process ban gaya:

1. **Step 1:** Customer name/email/phone/password enter karey → account create hota hai + 6-digit code email hota hai
2. **Step 2:** Customer code verify karey → account active hota hai + login session ban jaata hai

## Flow

```
Customer taps "Create Account"
    ↓
Fills name, email, phone, password
    ↓
Server: Account create + OTP email send
    ↓
Browser: "Enter the code we emailed you" screen
    ↓
Customer: 6-digit code enter karey
    ↓
Server: OTP verify + session create
    ↓
Welcome page!
```

## Backend Changes

### New Endpoints

**`POST /api/auth/signup`** (updated)
- Pehle: Immediately create session + return user
- Ab: Account create, OTP issue, return `{otpRequired: true, email, emailHint, message}`
- Same validation, same rate limiting

**`POST /api/auth/signup/verify-otp`** (new)
- Input: `{email, otp}`
- Checks: OTP match, not expired, not locked out
- On success: session create + return user
- Rate-limited per email

**`POST /api/auth/signup/resend-otp`** (new)
- Input: `{email}`
- Issues fresh code (resets attempt counter)
- Rate-limited (can't spam)
- Safe response (never reveals if email exists)

### Database

No schema changes needed! The same `login_otp_code`, `login_otp_expires`, `login_otp_attempts` columns handle both signup and login OTPs — no conflict because flows are sequential (can't signup and login to same email at once).

```
User → login_otp_code, login_otp_expires, login_otp_attempts
       ↑
       Used for both login OTP verification AND signup OTP verification
```

## Frontend Changes

### New Modal Screens

**Signup OTP Form** (after account creation)
- 6-digit code input
- "Verify & Activate Account" button
- "Resend code" link
- "Try again" link (back to signup form)

All styled with your Ivory/Burgundy/Gold theme — matches login OTP UI exactly.

### JavaScript

New variables & handlers:
- `pendingSignupEmail` — holds email between creation and verification
- `signupOtpForm` listener — verifies code, creates session
- `resendSignupOtpLink` listener — resends code
- `backToSignUpFromOtp` listener — back button
- `switchAuthTab('signup-otp')` — new tab state

Input filter runs on `signupOtpCode` too — strips non-digits in real time, Safari compatible.

## Testing Checklist

- [ ] Create new account → OTP email arrives
- [ ] Enter correct code → Account activates, login succeeds
- [ ] Enter wrong code → Error message, stays on OTP screen
- [ ] Wait 5 minutes → "Code expired" message
- [ ] Try 5+ times → "Too many attempts, request new code"
- [ ] Click "Resend code" → Fresh code emailed
- [ ] Click "Try again" → Back to signup form
- [ ] Paste "123 456" → Auto-strips to "123456"
- [ ] Try on Safari → Works smoothly

## Configuration

No new env vars needed. OTP uses existing MAIL_* settings:
- Email goes to customer's signup address
- Admin alert goes to `ADMIN_NOTIFY_EMAIL` (defaults to `MAIL_USERNAME`)

```bash
export MAIL_SERVER="smtp.gmail.com"
export MAIL_PORT=587
export MAIL_USE_TLS=true
export MAIL_USERNAME="your-gmail@gmail.com"
export MAIL_PASSWORD="your-app-password"
```

## What Stayed the Same

- Existing accounts can still login (just with OTP verification now)
- Cart, wishlist, orders — all unchanged
- Admin panel — unchanged
- Product catalog — unchanged
- Everything else works exactly as before

The only change visible to customers: They now verify their email during signup + provide OTP during every login. That's it. 🎉
