package jobs

import (
	"bytes"
	"testing"
	"time"
)

func TestNewULIDFormat(t *testing.T) {
	id, err := NewULID(time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if !ValidJobID(id) {
		t.Errorf("%q is not a valid ULID", id)
	}
}

func TestULIDsSortByTime(t *testing.T) {
	zeros := bytes.NewReader(make([]byte, 10))
	ones := bytes.NewReader(bytes.Repeat([]byte{0xff}, 10))
	early, _ := newULID(time.UnixMilli(1_000), ones)
	late, _ := newULID(time.UnixMilli(1_001), zeros)
	if !(early < late) {
		t.Errorf("%s should sort before %s", early, late)
	}
}

func TestULIDKnownValue(t *testing.T) {
	id, _ := newULID(time.UnixMilli(0), bytes.NewReader(make([]byte, 10)))
	if id != "00000000000000000000000000" {
		t.Errorf("got %s", id)
	}
}
