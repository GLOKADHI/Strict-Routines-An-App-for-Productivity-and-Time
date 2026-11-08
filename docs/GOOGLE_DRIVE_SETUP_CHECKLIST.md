# StrictRecords — Unified Google Sign-In & Google Drive Cloud Storage Setup Checklist

This document provides the authoritative, production-grade configuration guide for setting up Google Authentication and Google Drive Cloud Storage for **StrictRecords** across both platforms:
- **Android APK** (`com.strictrecords.app`)
- **Windows Desktop Application** (`StrictRecords.exe`)

---

## 1. Dedicated Google Account Requirements

StrictRecords synchronizes sensitive accountability records, attendance proofs, gym evidence, and schedules across your devices. A dedicated Google account must be used specifically for StrictRecords.

### Account Criteria
1. **Official Account Creation**: Create the account via [Google Accounts](https://accounts.google.com/signup).
2. **Dedicated Purpose**: Do not use your primary personal or work account. This isolates evidence files and limits access to accountability records.
3. **Storage Verification (Minimum 5 GB Free)**:
   - Google Drive provides 15 GB free by default across Gmail, Drive, and Google Photos.
   - StrictRecords enforces a strict **minimum 5 GB available free storage** rule before full cloud backup is enabled.
   - If free storage is below 5 GB, both the Android APK and Windows EXE will display a high-priority storage warning and pause non-critical automated media uploads until space is freed.
4. **Single Identity Across Devices**: Both the Android APK and Windows EXE must be signed into this exact same Google account.

---

## 2. Google Cloud Console Setup

### Step 2.1: Create a Google Cloud Project
1. Log into the [Google Cloud Console](https://console.cloud.google.com/) with your dedicated Google account.
2. Click the project dropdown at the top and select **New Project**.
3. Set the Project Name: `StrictRecords Cloud` (or your preferred name).
4. Note your **Project ID** and click **Create**.

### Step 2.2: Enable Google Drive API
1. Navigate to **APIs & Services > Library**.
2. In the search box, search for `Google Drive API`.
3. Click on **Google Drive API** and click **Enable**.
4. (Optional) Also verify that `Google Identity Services` / `OAuth2 API` is enabled.

---

## 3. OAuth Consent Screen Configuration

1. In the Google Cloud Console, navigate to **APIs & Services > OAuth consent screen**.
2. Select **User Type**: **External** (standard for non-Google Workspace consumer accounts), then click **Create**.
3. **App Information**:
   - **App name**: `StrictRecords`
   - **User support email**: Select your dedicated Google account email.
   - **Developer contact information**: Enter your dedicated Google account email.
4. **Scopes**:
   Click **Add or Remove Scopes** and add:
   - `https://www.googleapis.com/auth/drive.file` — *View and manage Google Drive files and folders that you have opened or created with this app.* (Least privilege; does not grant access to personal Drive files).
   - `openid` — *Associate you with your personal info on Google.*
   - `https://www.googleapis.com/auth/userinfo.email` — *View your email address.*
   - `https://www.googleapis.com/auth/userinfo.profile` — *View your basic profile info.*
5. **Test Users**:
   - While the app is in "Testing" publishing status, you **must** add your dedicated Google account email to the **Test Users** list.
   - Click **Save and Continue**.

---

## 4. OAuth 2.0 Credentials Setup

### Step 4.1: Windows Desktop Client ID (PKCE Flow)
1. Navigate to **APIs & Services > Credentials**.
2. Click **+ Create Credentials > OAuth client ID**.
3. Select **Application type**: **Desktop app**.
4. Set the **Name**: `StrictRecords Windows Desktop`.
5. Click **Create**.
6. Google will generate:
   - **Client ID** (e.g., `xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.apps.googleusercontent.com`)
   - **Client Secret** (Desktop PKCE utilizes RFC 8252 loopback receiver; client secret is treated as public per OAuth 2.0 specification).
7. Download or copy the Client ID.
8. Save this in `data/google_client_config.json` on Windows (or configure it via environment variable `STRICTRECORDS_GOOGLE_CLIENT_ID`):
   ```json
   {
     "client_id": "YOUR_DESKTOP_CLIENT_ID.apps.googleusercontent.com",
     "client_secret": "YOUR_CLIENT_SECRET",
     "auth_uri": "https://accounts.google.com/o/oauth2/auth",
     "token_uri": "https://oauth2.googleapis.com/token",
     "redirect_uris": ["http://127.0.0.1"]
   }
   ```

### Step 4.2: Android Client ID (Cryptographic App Identity)
1. Under **APIs & Services > Credentials**, click **+ Create Credentials > OAuth client ID**.
2. Select **Application type**: **Android**.
3. Enter the package details:
   - **Name**: `StrictRecords Android APK`
   - **Package name**: `com.strictrecords.app`
   - **SHA-1 certificate fingerprint**:
     ```
     76:5E:B7:F9:3A:57:54:55:77:7B:30:6D:60:07:6C:68:58:70:F8:B8
     ```
     *(Note: If signing with a custom release keystore in production, retrieve its SHA-1 using `keytool -list -v -keystore your_release.keystore` and add that SHA-1 fingerprint as well).*
4. Click **Create**.

---

## 5. Storage Hierarchy & Architecture

StrictRecords organizes cloud data inside an isolated root application directory in your Google Drive:

```
StrictRecords/
├── Evidence/
│   ├── Images/       (Wake-up, Bath, Movie QR, App proofs: JPG, PNG, WebP)
│   ├── Videos/       (Gym workout videos, physical movement recordings: MP4)
│   └── Documents/    (Receipts, booking PDFs, logs)
├── Schedules/        (Daily smart schedule records & attachments: JSON/CSV)
├── Attendance/       (Hourly verification logs, sleep analysis reports)
├── Reports/          (OCR results, face detection audit logs)
├── Metadata/         (Integrity digests, SHA-256 indexes, device manifests)
└── Sync/             (Cross-device event synchronization journal)
```

### Technical Upload Protocols
- **Files < 5 MB**: Uploaded using direct multipart HTTP POST.
- **Files >= 5 MB**: Uploaded using Google Drive Resumable Upload protocol (`uploadType=resumable`) with 256 KB chunking and byte-range retry capabilities.
- **Integrity**: SHA-256 and MD5 hashes are validated for every file upon upload and download.
- **Authoritative Source**: The local SQLite database is the authoritative source for scores, schedules, and deduction logic. Drive serves as an immutable cloud backup and cross-device sync fabric.

---

## 6. Secure Token Persistence

- **Windows Desktop**: Tokens (`access_token`, `refresh_token`, expiry, email) are encrypted using the Windows Data Protection API (**DPAPI** via `CryptProtectData`), tied to the user's Windows login session, and stored in `data/google_session.enc`.
- **Android APK**: Tokens are securely stored within private app storage (`MODE_PRIVATE` SharedPreferences and application database), inaccessible to external applications.

---

## 7. Operational Verification Checklist

Before deploying or operating:
- [ ] Dedicated Google account created and verified.
- [ ] Google Drive storage checked: **at least 5 GB available**.
- [ ] Google Cloud Project created and Google Drive API v3 enabled.
- [ ] OAuth Consent Screen configured with `drive.file`, `openid`, `email`, and `profile` scopes.
- [ ] Dedicated email added to **Test Users** on the OAuth consent screen.
- [ ] Desktop OAuth client created; credentials loaded in Windows application.
- [ ] Android OAuth client created with package `com.strictrecords.app` and SHA-1.
- [ ] Signed into the same account on Windows EXE and verified "Account Connected" with active quota bar.
- [ ] Signed into the same account on Android APK and verified "Account Connected" with active quota bar.
- [ ] Evidence files (wake-up picture, gym video) uploaded and visible in Google Drive under `StrictRecords/Evidence/`.
- [ ] Cross-device file availability confirmed between Windows and Android.
