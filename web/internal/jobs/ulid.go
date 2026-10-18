package jobs

import (
	"crypto/rand"
	"io"
	"math/big"
	"time"
)

const crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

// NewULID returns a ULID: 48 bits of millisecond time followed by 80 random bits,
// in Crockford base32. IDs created later sort after earlier ones.
// Written here to avoid a dependency for ~20 lines of code.
func NewULID(t time.Time) (string, error) { return newULID(t, rand.Reader) }

func newULID(t time.Time, entropy io.Reader) (string, error) {
	var b [16]byte
	ms := uint64(t.UnixMilli())
	for i := 5; i >= 0; i-- {
		b[i] = byte(ms)
		ms >>= 8
	}
	if _, err := io.ReadFull(entropy, b[6:]); err != nil {
		return "", err
	}
	n := new(big.Int).SetBytes(b[:])
	out := make([]byte, 26)
	mask := big.NewInt(31)
	for i := 25; i >= 0; i-- {
		out[i] = crockford[new(big.Int).And(n, mask).Int64()]
		n.Rsh(n, 5)
	}
	return string(out), nil
}
