# HomeBase 3 archive channel findings

Observed during read-only archive retrieval on 2026-11-09.

An archive request's channel and its returned media channel can differ. A camera currently assigned channel 1 returned historical January/February 2024 recordings as video command 1300 and audio command 1301, data type 3, on channel 0. Filtering media by the camera's current channel silently discarded all packets and produced a misleading authentication/assembly failure.

Accept the single media channel observed during an exclusive archive request. Reject mixed media channels. Keep the archive path unchanged. Preserve authenticated keyframe decryption, access-unit assembly, expected picture count, and full video decode validation. One previously failing recording was recovered with all 291 expected pictures and 287 audio packets; subsequent retries also produced verified recordings.

Removed-camera probe: submitting the original stored path on station request channel 0 produced 219 video packets and 227 audio packets on media channel 1. This establishes that the current cloud device list is not required to receive its media. Subsequent end-to-end recovery of that recording passed eight authenticated keyframes, all 219 expected pictures, 227 audio packets, and full FFmpeg decoding. Packet arrival alone was not treated as success. The downloader uses station channel 0 when the archived camera is absent and still requires all normal validation before publication. Reject rows belonging to another station.

The private diagnostic summary records only counts by command, data type, and channel. Raw captures, archive paths, keys, credentials, and device identifiers remain outside the public repository.

## ECC scalar width

Node's ECDH private scalar can omit leading zero bytes. Pad it on the left to exactly 32 bytes before passing it to the SDK's P-256 decoder. Padding preserves its public key; it does not change the scalar or relax cryptographic verification.

## Validation

Checked historical channel 0 with request channel 1, media channel 1 with station request channel 0, rejection of mixed media channels, and public-key preservation for a short P-256 scalar. Live archive recovery additionally passed authentication, exact expected picture count, and full FFmpeg decoding.

## Resolution changes and picture preservation

Verified on 2026-11-10: a complete 891-picture HEVC recording decoded cleanly, but the three-position sample check produced only two JPEGs. FFmpeg's filter graph reinitialized when the input resolution changed, resetting the `select` filter's frame counter. Keeping `-reinit_filter 0` on the sample-check input preserved that counter and produced all three expected samples. The saved authenticated transfer then passed publication checks without downloading again.

The downloader also checks the MP4 video stream's `nb_frames` against the authenticated, assembled picture count before publishing. Two other cleanly decodable MP4s contained 229 of 259 and 120 of 150 assembled pictures respectively. Their elementary streams begin without complete HEVC initialization/keyframe NAL units. Their transfers remain incomplete for publication; a clean FFmpeg exit alone does not establish preservation of every picture. This check does not recover missing initial pictures or relax keyframe authentication.
