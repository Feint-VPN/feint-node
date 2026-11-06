package hysteria

import (
	"errors"
	"io"
	"net"
	"os"

	"github.com/apernet/quic-go"
)

func newServerTransport(conn net.PacketConn) (*quic.Transport, error) {
	transport := &quic.Transport{Conn: conn}
	path := os.Getenv("XRAY_HYSTERIA_STATELESS_RESET_KEY_FILE")
	if path == "" {
		return transport, nil
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, errors.New("cannot open Hysteria stateless reset key file")
	}
	defer file.Close()
	var key quic.StatelessResetKey
	data, err := io.ReadAll(io.LimitReader(file, int64(len(key)+1)))
	if err != nil || len(data) != len(key) {
		return nil, errors.New("Hysteria stateless reset key file must contain exactly 32 raw bytes")
	}
	copy(key[:], data)
	transport.StatelessResetKey = &key
	return transport, nil
}
