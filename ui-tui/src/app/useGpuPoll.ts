import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import type { GatewayClient } from '../gatewayClient.js'
import type { SystemGpuResponse } from '../gatewayTypes.js'
import { asRpcResult } from '../lib/rpc.js'

import type { BatteryCategory, GpuInfo } from './interfaces.js'
import { $uiState, patchUiState } from './uiStore.js'

const GPU_POLL_MS = 30_000

const CATEGORIES: ReadonlySet<BatteryCategory> = new Set(['bad', 'critical', 'dim', 'good', 'warn'])

const normalizeCategory = (raw: unknown): BatteryCategory =>
  typeof raw === 'string' && CATEGORIES.has(raw as BatteryCategory) ? (raw as BatteryCategory) : 'dim'

const toFiniteInt = (v: unknown): null | number =>
  typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.round(v) : null

/** Coerce a `system.gpu` RPC payload into the UI's GpuInfo shape. */
export const toGpuInfo = (r: null | SystemGpuResponse): GpuInfo | null => {
  if (!r) {
    return null
  }

  return {
    available: !!r.available,
    category: normalizeCategory(r.category),
    name: typeof r.name === 'string' && r.name ? r.name : null,
    total_mib: toFiniteInt(r.total_mib),
    used_mib: toFiniteInt(r.used_mib)
  }
}

/**
 * Poll host GPU VRAM while the status-bar indicator is enabled.
 *
 * Mirrors useBatteryPoll: system property, no `sid` gate, Python memoises the
 * read (30s TTL). A 30s cadence is plenty for the footer; failed polls keep
 * the last good reading so the segment does not disappear.
 */
export function useGpuPoll(gw: GatewayClient) {
  const enabled = useStore($uiState).gpu

  useEffect(() => {
    if (!enabled) {
      patchUiState({ gpuStatus: null })

      return
    }

    let cancelled = false

    const poll = async () => {
      try {
        const r = asRpcResult<SystemGpuResponse>(await gw.request<SystemGpuResponse>('system.gpu', {}))

        if (!cancelled) {
          const info = toGpuInfo(r)

          if (info?.available && info.used_mib != null && info.total_mib != null) {
            patchUiState({ gpuStatus: info })
          }
        }
      } catch {
        // Keep the last-good reading on a transient RPC failure.
      }
    }

    void poll()
    const id = setInterval(() => void poll(), GPU_POLL_MS)

    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [enabled, gw])
}
