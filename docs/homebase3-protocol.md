# HomeBase 3 recording retrieval investigation

Verification recorded: 2026-11-07.

Status: authentication, direct P2P connection, recording-day counts, and current-day and historical
recording queries have been verified directly on a T8030. The app's download request has been
authenticated and decoded, and a phone-free transfer has been received and decoded successfully.
Complete archive enumeration and robust bulk retrieval remain under investigation. A successful table read is **not** proof of complete archive access.

This note contains synthetic identifiers only. Account credentials, tokens, cipher keys, device
identifiers, addresses, raw captures, and private recording metadata are excluded.

## Environment and evidence

- Vendor Android app: `6.1.20_31181`.
- HomeBase model: `T8030`; database replies report version `1.2.0.1`.
- SDK under test: `@mega-yfue/eufy-sdk` `0.5.0`.
- Evidence: authenticated requests to a real HomeBase, the app's own local P2P traffic, JADX output
  for the readable portion of the APK, and ARM64 disassembly of `libmega_media_sdk.so`.
- The APK uses an Ijiami loader (`s.h.e.l.l.S` / `s.h.e.l.l.N`, `libexec.so`, and encrypted assets).
  The readable Java output is not a complete decompilation of the app's business logic. The native
  media library retains useful exported function names.

## Authentication and session setup

The existing `EufyMega` login flow works for the tested account. Its authenticated device registry
supplies the HomeBase record, its attached cameras, station channel metadata, and the owning
`member.admin_user_id`. Use the station record returned by `getDevices()` for that raw metadata;
the capability-bound `Device` returned by `getDevice()` does not expose that same `raw` property.

The implementation already lives in
[`mega-client.ts`](https://github.com/mhariri/eufy-sdk/blob/d81bb0f1422d439d142e9269626997332f70ae24/src/transport/http/mega-client.ts),
[`codec.ts`](https://github.com/mhariri/eufy-sdk/blob/d81bb0f1422d439d142e9269626997332f70ae24/src/transport/p2p/codec.ts), and
[`p2p-session.ts`](https://github.com/mhariri/eufy-sdk/blob/d81bb0f1422d439d142e9269626997332f70ae24/src/transport/p2p/p2p-session.ts):

1. Log in through the regional passport service using the SDK's signed HTTP / ECDH flow. Handle the
   discriminated captcha and 2FA outcomes in the same process that requested the challenge.
2. Persist the session with `FileSessionStore`; its file is written with mode `0600`. Keep its
   containing directory private as well.
3. Open only the requested station with `autoRealtime: false` and `connectStation(stationSn)`.
   The actual PPCS-discovered peer is authoritative; a cloud-reported LAN address can be stale.
4. `CMD_GATEWAYINFO` (`1100`) supplies a cipher ID and wrapped session-key material.
5. The SDK retrieves the account-authorized cipher through
   `/v3/app/cipher/get_ciphers` on the security-app host. Its request fields are `cipher_ids`,
   `user_id` (the station's admin ID), and `station_sn`.
6. The cipher's ECC private key unwraps the ECIES envelope through
   `deriveLevel2KeyFromGatewayInfo`. The resulting session key is distinct from the per-recording
   media key.

The existing HTTP implementation resolves the region with `estimate_domain`, performs a regional
ECDH exchange on `app-openapi-{region}.eufy.com`, and logs in at `/passport/login` on
`app-passport-{region}.eufy.com`. The login fields include `email`, an encrypted `password`,
`ab` (country), and `client_secret_info.public_key`. Signed requests carry `x-encryption-info:
algo_ecdh`, `x-key-ident`, `x-request-ts`, `x-request-once`, and `x-signature`. HTTP bodies use
AES-128-CBC with the negotiated share key; the signature covers timestamp, nonce, and encrypted
body. These details are existing SDK implementation references, not a new Android TLS transcript.

For the observed P2P gateway reply, decrypt sign-1 command data with the SDK's AES-128-ECB
level-1 helper. `p2pCommandEncryptionKey(stationSn, p2pDid)` derives that key from both identifiers;
the P2P DID is consequently sensitive even without an account password. The decrypted gateway
payload has its cipher ID as uint16 little-endian at offset 0 and a 129-byte ECIES envelope
starting at offset 4. Pass it and the authorized ECC private key to
`deriveLevel2KeyFromGatewayInfo`; do not substitute a recording row's cipher ID for the gateway's.

Steps 1–3 and the table access below have been exercised directly. Steps 4–6 were also used to
recover two captured app connections' session keys. The app's sign-8 requests and the station's
sign-2 responses decrypted with valid GCM authentication tags, confirming the session-key path
against the current app. Retain a stable persisted client identity when reusing an HTTP session.

## Recording metadata: a working request

`P2PSession.readDatabase("history_record_info", { accountId })` returned actual recording rows.
This uses inner command **10000**, not `queryRecordPage()`'s inner command **10017**.

The existing `queryDatabase` implementation sends a level-1 encrypted `CMD_SET_PAYLOAD` (`1350`)
on station channel **255**, wrapping `CMD_DATABASE` (`1306`). The query options are nested in a
second `payload`, rather than flattened into the database envelope:

```json
{
  "account_id": "0000000000000000000000000000000000000000",
  "cmd": 1306,
  "mChannel": 255,
  "mValue3": 0,
  "payload": {
    "cmd": 10000,
    "table": "history_record_info",
    "payload": {
      "count": 2000,
      "start_date": "",
      "end_date": "",
      "start_id": 0,
      "end_id": 1,
      "flag": 0,
      "need_ai": 1,
      "res_unzip": 1,
      "update_time": "0",
      "start_time": "0",
      "alarm_id": ""
    },
    "transaction": "<timestamp>"
  }
}
```

The successful reply has `cmd: 10000`, `mIntRet: 0`, `table: "history_record_info"`, a `count`,
and a flat `data` array of recording rows. These are **not** the grouped
`{table_name,payload}` rows expected by the HomeBase 2 archive example. Rows include:

`record_id`, `device_sn`, `station_sn`, `start_time`, `end_time`, `storage_path`, `thumb_path`,
`cipher_id`, `frame_num`, `folder_size`, `storage_type`, `storage_cloud`, and video dimensions.

The tested HomeBase names stored video files with a `.zxvideo` suffix. Do not manufacture a
HomeBase 2 `.dat` path from a recording timestamp: retain the path the HomeBase returned.

### Completeness and unsuccessful queries

- The default full-table request returned records for the current day. It has not established
  complete archive coverage.
- Supplying a broad `start_date` / `end_date` to that same full-table shape returned the same
  current-day set. Those fields alone are not a verified archive pagination mechanism.
- Response `start_id` and `end_id` were both `2147483647`; they are not a proven older-page cursor.
- `queryRecordPage()` (inner **10017**) was explicitly refused with **-6006**.
- Experimental inner **10009** / **10011** reads using the level-1 sender produced no complete
  reply. The app capture establishes that **10011 uses the level-2 GCM sender** instead. A
  level-1 timeout must not be reported as evidence that HomeBase 3 lacks that query.

Keep original rows and a coverage manifest. Never interpret an empty or refused query as evidence
that a day has no recordings. Verify date boundaries, camera coverage, retention boundaries,
pagination termination, and duplicate handling against the app before claiming a complete archive.

## App-verified archive query: command 10011

Authenticated decryption recovered both a current-day request and an older-day request from the
app. Both use outer `CMD_SET_PAYLOAD` **1350**, frame channel **255**, sign code **8**, and stream
ID **0**. The decrypted JSON is:

```json
{
  "account_id": "0000000000000000000000000000000000000000",
  "cmd": 1306,
  "mChannel": 255,
  "mValue3": 0,
  "payload": {
    "table": "history_record_info",
    "cmd": 10011,
    "payload": {
      "count": 30,
      "end_date": "20300103",
      "end_id": 1,
      "flag": 0,
      "need_ai": 1,
      "res_unzip": 1,
      "start_date": "20300102",
      "start_id": 0,
      "update_time": "0",
      "start_time": "0",
      "alarm_id": ""
    },
    "transaction": "<timestamp>"
  }
}
```

The app supplies the selected day and the next day as date boundaries. Their exact timezone and
inclusivity at midnight still require boundary tests. Reproduce this with `sendRawLevel2` after
`awaitLevel2Key`; changing only `readDatabase`'s `innerCmd` leaves the wrong encryption path.
Attach response listeners before sending and serialize requests within a station session.

The **1306** reply contains `cmd: 10011`, `mIntRet: 0`, the transaction, `start_id`, `end_id`, and
`data`. Unlike the flat 10000 response, its `data` is a list of `{table_name, payload}` groups:

- `history_record_info`: recording rows and original storage paths.
- `record_crop_picture_info` and `record_heat_map_info`: associated metadata.
- `person_basic_info`, `face_feature_info`, and `reid_feature_info`: recognition metadata.

Treat recognition metadata as private account data; it is unnecessary for a public protocol
fixture. An empty result can encode `data` as the string `"[]"`. Preserve returned cursor values
as integers with sufficient precision, but do not infer pagination semantics from their names.
Both captured and directly reproduced older-day requests returned recordings. Direct reproduction
returned 16 current-day rows and 15 older-day rows, with successful grouped responses and matching
transactions. This verifies historical metadata access without the phone. It does not establish
full retention coverage or termination for days exceeding 30 rows.

### App-verified day counts: command 10008

The app uses the same outer frame and database envelope with inner **10008** and this payload:

```json
{
  "start_date": "20300101",
  "end_date": "20300131"
}
```

The successful response has `cmd: 10008`, `mIntRet: 0`, `count`, and `data` entries containing
`days` and `count`. A direct SDK reproduction returned seven recording-day entries for the tested
month. This verifies calendar inventory access, but not an archive-wide enumeration strategy.
Verify date format, boundary handling, retention discovery, and whether counts cover all station
channels before using it to certify completeness.

## Recording transfer and decryption

The SDK's current `camera.downloadRecording` is deliberately gated to HomeBase 2. Its
`homeBase2RecordingPath`, string-pair sender, and archive frame decoder are useful references, not
an implementation of the HomeBase 3 path. Removing the family gate is not a verified fix.

### App-verified transfer request

An actual download was captured for a camera attached to T8030. The authenticated request uses
outer **1350**, frame channel **1**, sign code **8**, and stream ID **13**. Its plaintext is:

```json
{
  "account_id": "0000000000000000000000000000000000000000",
  "cmd": 1024,
  "mChannel": 1,
  "mValue3": 1024,
  "payload": {
    "filepath": "<storage_path returned by history_record_info>",
    "key": "<128 hexadecimal characters>"
  }
}
```

The key field is the client's P-256 public coordinate pair in hexadecimal form, without the
uncompressed point's leading `04` byte. A generated pair was verified by successful transfer and
authenticated keyframe decryption; it is not an RSA modulus or the per-stream AES key.

The app's playback request uses inner **1025**, `mValue3: 1025`, `mChannel: 0`, and the same two
payload fields; its captured outer frame also uses channel 1, sign 8, stream 13. Frame channel,
JSON `mChannel`, and stream ID are distinct fields. Their values above describe one observed
camera and transfer; no mapping for other channels is established yet.

The captured download includes video command **1300** on binary data type **3**, transfer-start
command **1303** on data type **2**, channel **255**, sign **2**, and transfer-finish **1304** on
data type **2**, channel **255**, sign **0**, with an empty payload. The existing HomeBase 2
receiver accepts finish channel 0 or the camera channel, so it would miss this channel-255 finish.
The current level-2 raw sender also defaults the outgoing stream ID to 0; matching the app's
transfer requires preserving its observed stream ID. Neither difference justifies an untested
change to every device family.

### Direct transfer verification

A direct request with a newly generated P-256 key pair succeeded. The request's `key` was the
uncompressed public point with its leading `04` byte removed (64 bytes, 128 hex characters).
The corresponding client private scalar authenticated all five keyframes through
`VideoFrameDecoder`; the other 145 frames carried plaintext media after their 22-byte headers.
A channel-255 finish marker completed the transfer. No recording-cipher private key was needed
for this transfer; the recording's cloud cipher lookup returned no entries.

The resulting elementary stream contained 150 frames, matched the recording metadata, was
identified as HEVC at 2304×2592 and 15 fps, and decoded through FFmpeg without errors. The
`h264` field name on the existing decoder does not establish the actual codec. The dual-view
picture contains two vertically stacked views, which analysis must both cover.

This verifies a single video-only transfer on the captured camera/channel. Audio, other camera
channel mappings, repeated transfers, and packet-loss recovery still require validation.

### Native media crypto evidence

The app's ARM64 `libmega_media_sdk.so` contains these exported functions:

- `zx_p2p_send_command`
- `zx_p2p_download_ecc_video_manager_V9`
- `zx_p2p_download_ecc_mediaparse_video_manager_V9`
- `zx_p2p_media_e2e_download_video_manager`

For the inspected app build, `zx_p2p_download_ecc_video_manager_V9` at `0x1fb28c` calls
`decrypt_AES_key` with the envelope at frame offset `0x16`, then calls
`aes_gcm256_decrypt_media`. Its argument setup references nonce offset `0xa7` and tag offset
`0x97`. These match the existing SDK video layout:

| Offset       | Meaning                                   |
| ------------ | ----------------------------------------- |
| `0x00..0x15` | 22-byte video-frame header                |
| `0x16..0x96` | 129-byte ECIES key envelope on a keyframe |
| `0x97..0xa6` | 16-byte GCM authentication tag            |
| `0xa7..0xb2` | 12-byte GCM nonce                         |
| `0xb3..`     | encrypted media payload                   |

This native evidence confirms the relevant crypto path; it does not by itself prove that every
HomeBase 3 archive frame has that layout or that all non-keyframes use the HomeBase 2 layout.

The existing P2P session code distinguishes two authenticated command-body layouts under the
negotiated GCM key, with AAD `eufy security`:

- sign code **8**, app to station: `tag[16] || nonce[12] || sequence[4] || ciphertext`.
- sign code **2**, station to app: `tag[16] || nonce[12] || ciphertext`.

These command-body wrappers are separate from the media-frame header and per-stream key envelope.
Accept a candidate decode only when its GCM tag authenticates.

## Bulk retrieval observations

A date-bounded 10011 query with `count: 2000` was used for every recording day returned by 10008.
All day queries completed after retrying a disconnected session, and no response reached that
requested limit. Broad multi-year 10011 requests did not reproduce the per-day results. Returned
cursor fields alone did not advance small-page tests; do not advertise them as working pagination.
The calendar's observed `count` values were not reliable recording totals; use actual returned rows.

Repeated video-plus-audio transfers work on attached camera channels 0 and 1 with fresh client
P-256 pairs. AAC audio authenticates under the preceding keyframe's media key and can be muxed
with the decoded video. Treat transfer-start 1303's five-byte plaintext as an opaque message until
its fields are established; reading its first four bytes as a signed error falsely rejects valid
transfers and can leave one transfer overlapping the next. Always receive the finish marker before
requesting another clip, and filter incoming archive frames by the requested camera channel.

A tested HEVC recording splits some pictures into a 400000-byte part and a remainder. Each part
has its own authenticated key envelope / GCM wrapper, but repeats the picture's sequence and
six-byte timestamp. Decode each part, concatenate consecutive parts with the same picture identity,
and write a complete picture to the elementary stream. The current fixed-64000-byte assembler
is insufficient for this recording. A saved transfer with 251 video chunks assembled into 247
pictures, matching its recording metadata; FFmpeg decoded the repaired stream without errors.
Do not confuse wire-chunk count with picture count. Keep other fragmentation cases unverified.

Use sequential station sessions for inventory and transfers. An inventory session disconnected
while another session opened; its remaining sends failed immediately, and retrying the missing
days in a separate session succeeded. This is an operational observation, not proof of a universal
HomeBase connection limit or its cause. Preserve a per-day checkpoint rather than interpreting
a failed query as an empty day.

### Rows without downloadable video

Calendar-linked metadata can include rows whose `storage_path` is empty and whose `frame_num`
and `folder_size` are both zero. Preserve these as metadata observations, but exclude them from
video download requests and video-count completeness checks. Do not hash an empty path into a
shared recording ID or send an empty download filepath. An unexpected row with video frames or
bytes but no path needs investigation rather than silent exclusion.

## Remaining verification before an SDK change

1. Extend the verified GCM-wrapped 10008 / 10011 queries to pagination beyond a single page,
   all retained dates, timezone boundaries, camera coverage, and calendar/row count agreement.
2. Verify whether other attached camera models use the same client-public-key transfer path.
3. Extend the successful command-1024 video transfer to muxed output and verified audio.
4. Confirm fragmented keyframes, non-keyframes, audio, timeouts, frame counts, and channel
   mapping before exposing HomeBase 3 `downloadRecording`. Retain the family gate until then.
5. Add synthetic byte-vector tests for the verified behavior and run the repository's required
   checks before proposing an upstream change.

Raw captures, authenticated command plaintext, session files, cipher responses, and recording
manifests stay outside the SDK repository. Public evidence should be a manually redacted field
transcript and synthetic fixtures; never publish a capture merely because video bytes were removed.
