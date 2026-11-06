package hysteria

import (
	"bytes"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

func TestFeintServerTransportResetKey(t *testing.T) {
	key := bytes.Repeat([]byte{0x73}, 32)
	directory := t.TempDir()
	valid := filepath.Join(directory, "reset.key")
	if err := os.WriteFile(valid, key, 0600); err != nil {
		t.Fatal(err)
	}
	conn, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	for _, test := range []struct {
		name string
		path string
	}{
		{"unset preserves upstream behavior", ""},
		{"configured file", valid},
		{"same file after recreation", valid},
	} {
		t.Run(test.name, func(t *testing.T) {
			t.Setenv("XRAY_HYSTERIA_STATELESS_RESET_KEY_FILE", test.path)
			transport, err := newServerTransport(conn)
			if err != nil {
				t.Fatal(err)
			}
			if transport.Conn != conn {
				t.Fatal("transport replaced the existing packet connection")
			}
			if test.path == "" {
				if transport.StatelessResetKey != nil {
					t.Fatal("unconfigured nodes must retain upstream behavior")
				}
			} else if transport.StatelessResetKey == nil || !bytes.Equal(transport.StatelessResetKey[:], key) {
				t.Fatal("transport did not retain the persistent key")
			}
		})
	}
}

func TestFeintServerTransportRejectsInvalidResetKey(t *testing.T) {
	for _, size := range []int{0, 31, 33, 4096} {
		t.Run(strconv.Itoa(size)+" bytes", func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "reset.key")
			if err := os.WriteFile(path, bytes.Repeat([]byte{0x73}, size), 0600); err != nil {
				t.Fatal(err)
			}
			t.Setenv("XRAY_HYSTERIA_STATELESS_RESET_KEY_FILE", path)
			if transport, err := newServerTransport(nil); err == nil || transport != nil {
				t.Fatal("malformed configured key must prevent transport creation")
			}
		})
	}
	for _, path := range []string{filepath.Join(t.TempDir(), "missing"), t.TempDir()} {
		t.Setenv("XRAY_HYSTERIA_STATELESS_RESET_KEY_FILE", path)
		if transport, err := newServerTransport(nil); err == nil || transport != nil {
			t.Fatal("unreadable configured key must prevent transport creation")
		}
	}
}
