// SPDX-License-Identifier: MPL-2.0
package trojan

import (
	"bytes"
	"io"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/xtls/xray-core/common/buf"
)

type firstChunkReader struct {
	data  []byte
	first int
}

func (r *firstChunkReader) Read(p []byte) (int, error) {
	if len(r.data) == 0 {
		return 0, io.EOF
	}
	limit := len(r.data)
	if r.first > 0 {
		limit = min(limit, r.first)
		r.first = 0
	}
	n := copy(p, r.data[:limit])
	r.data = r.data[n:]
	return n, nil
}

func TestVpsControllerTrojanFragmentedHeader(t *testing.T) {
	payload := []byte(strings.Repeat("a", 56) + "\r\n" + "payload")
	for _, split := range []int{1, 2, 55, 56, 57, 58, len(payload)} {
		reader := &firstChunkReader{data: append([]byte(nil), payload...), first: split}
		first := buf.New()
		n, err := readFirstTrojanRequest(reader, first)
		if err != nil || n < 58 {
			t.Fatalf("split %d: length=%d, err=%v", split, n, err)
		}
		rest, _ := io.ReadAll(reader)
		actual := append(append([]byte(nil), first.Bytes()...), rest...)
		first.Release()
		if !bytes.Equal(actual, payload) {
			t.Fatalf("split %d: header/payload changed", split)
		}
	}
}

func TestVpsControllerTrojanFallbackAndTruncatedHeader(t *testing.T) {
	for _, data := range [][]byte{[]byte("GET / HTTP/1.1"), {0x16, 3, 1}} {
		reader := &firstChunkReader{data: data, first: 1}
		first := buf.New()
		n, err := readFirstTrojanRequest(reader, first)
		first.Release()
		if err != nil || n != 1 || len(reader.data) != len(data)-1 {
			t.Fatal("fallback consumed extra data")
		}
	}
	first := buf.New()
	defer first.Release()
	_, err := readFirstTrojanRequest(bytes.NewReader([]byte("abc")), first)
	if err != io.ErrUnexpectedEOF && err != io.EOF {
		t.Fatalf("truncated header accepted: %v", err)
	}
}

func TestVpsControllerTrojanHandshakeDeadline(t *testing.T) {
	server, client := net.Pipe()
	defer server.Close()
	defer client.Close()
	server.SetReadDeadline(time.Now().Add(50 * time.Millisecond))
	go client.Write([]byte("a"))
	first := buf.New()
	defer first.Release()
	_, err := readFirstTrojanRequest(server, first)
	if timeout, ok := err.(net.Error); !ok || !timeout.Timeout() {
		t.Fatalf("handshake deadline lost: %v", err)
	}
}
