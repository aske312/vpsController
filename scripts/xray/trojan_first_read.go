// SPDX-License-Identifier: MPL-2.0
// vpsController compatibility fix for fragmented Trojan authentication headers.
package trojan

import (
	"io"

	"github.com/xtls/xray-core/common/buf"
)

func readFirstTrojanRequest(reader io.Reader, first *buf.Buffer) (int64, error) {
	n, err := first.ReadFrom(reader)
	if err != nil || n == 0 || n >= 58 {
		return n, err
	}
	// Only a possible SHA-224 authentication prefix needs more bytes. Leave
	// non-Trojan traffic to the existing fallback parser without extra waiting.
	for _, c := range first.BytesTo(min(int32(n), 56)) {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			return n, nil
		}
	}
	// The caller's handshake deadline still bounds this read. Authentication,
	// CRLF checks and all routing remain in Xray's original Trojan handler.
	more, err := first.ReadFullFrom(reader, 58-int32(n))
	return n + more, err
}
