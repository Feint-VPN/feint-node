package bbr

import (
	"testing"

	"github.com/apernet/quic-go/congestion"
	"github.com/stretchr/testify/require"
)

func TestFeintBBRHandlesMTUDecrease(t *testing.T) {
	for _, profile := range []Profile{ProfileConservative, ProfileStandard, ProfileAggressive} {
		t.Run(string(profile), func(t *testing.T) {
			b := NewBbrSender(DefaultClock{}, 1280, profile)
			require.NotPanics(t, func() { b.SetMaxDatagramSize(1200) })
			require.Equal(t, congestion.ByteCount(1200), b.maxDatagramSize)
			require.Equal(t, congestion.ByteCount(32*1200), b.initialCongestionWindow)
			require.Equal(t, b.initialCongestionWindow, b.congestionWindow)
			require.Equal(t, congestion.ByteCount(4*1200), b.minCongestionWindow)
			require.Equal(t, congestion.ByteCount(congestion.MaxCongestionWindowPackets*1200), b.maxCongestionWindow)
			require.Equal(t, b.maxCongestionWindow, b.recoveryWindow)
		})
	}
}

func TestFeintBBRHandlesMTUDecreaseAfterDiscovery(t *testing.T) {
	b := NewBbrSender(DefaultClock{}, 1200, ProfileStandard)
	b.SetMaxDatagramSize(1400)
	b.congestionWindow = b.maxCongestionWindow
	b.recoveryWindow = b.maxCongestionWindow
	require.NotPanics(t, func() { b.SetMaxDatagramSize(1200) })
	require.Equal(t, b.maxCongestionWindow, b.congestionWindow)
	require.Equal(t, b.maxCongestionWindow, b.recoveryWindow)
	require.True(t, b.CanSend(b.congestionWindow-1200))
	require.False(t, b.CanSend(b.congestionWindow))
	window := b.congestionWindow
	b.SetMaxDatagramSize(1200)
	require.Equal(t, window, b.congestionWindow)
}
