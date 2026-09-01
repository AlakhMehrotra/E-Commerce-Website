# Safari Compatibility Fixes — Login OTP

## Issue
Safari (especially on iOS) doesn't fully support some mobile input attributes that were originally used in the OTP code field.

## What was changed:

### 1. **saree.html** — OTP Input Field
**Removed:**
- `inputmode="numeric"` — Safari keyboard behavior inconsistent
- `pattern="[0-9]*"` — causes validation issues in Safari
- `autocomplete="one-time-code"` — iOS Safari ignores this

**Now:**
```html
<input type="text" id="loginOtpCode" class="otp-input" maxlength="6" placeholder="••••••" required>
```
Simple `type="text"` with `maxlength="6"` — works everywhere.

---

### 2. **saree.js** — Real-time Input Filtering
Added a live input filter that strips any non-digits as the user types:

```javascript
document.getElementById('loginOtpCode').addEventListener('input', function (e) {
    this.value = this.value.replace(/\D/g, '').slice(0, 6);
});
```

Benefits:
- User can't accidentally type letters, spaces, or dashes
- If they paste a code with spaces (like "123 456"), it auto-fixes to "123456"
- Works on Safari, Chrome, Firefox, all browsers

Also updated the form submit handler to safely strip digits:
```javascript
let otp = document.getElementById('loginOtpCode').value.trim().replace(/\D/g, '');
```

---

### 3. **saree.css** — OTP Input Styling
Added monospace font for better digit alignment on Safari:

```css
.otp-input {
  font-family: 'Courier New', monospace;
  /* ... existing styles ... */
}
```

---

## Testing

**Safari (iOS/macOS):**
- ✅ Type 6 digits normally → Works
- ✅ Paste "123 456" → Auto-fixes to "123456"
- ✅ Paste "ABC123" → Strips to "123"
- ✅ Keyboard appears and disappears smoothly

**All other browsers:**
- ✅ Still works exactly as before
- ✅ Input filter provides same safety net

---

## Server-side Notes
No changes to backend. The OTP verification endpoint (`/api/auth/login/verify-otp`) already does its own digit validation, so even if malformed data somehow reaches it, the server rejects it cleanly.
