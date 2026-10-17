import { describe, expect, it } from 'vitest'

import { toGpuInfo } from '../app/useGpuPoll.js'

describe('toGpuInfo', () => {
  it('returns null for a null payload', () => {
    expect(toGpuInfo(null)).toBeNull()
  })

  it('maps a full reading through faithfully', () => {
    expect(
      toGpuInfo({
        available: true,
        category: 'warn',
        name: 'NVIDIA GeForce RTX 5090',
        total_mib: 32607,
        used_mib: 19442
      })
    ).toEqual({
      available: true,
      category: 'warn',
      name: 'NVIDIA GeForce RTX 5090',
      total_mib: 32607,
      used_mib: 19442
    })
  })

  it('rounds MiB values', () => {
    expect(toGpuInfo({ available: true, category: 'good', total_mib: 100.6, used_mib: 10.4 })?.used_mib).toBe(10)
  })

  it('coerces missing MiB to null', () => {
    expect(toGpuInfo({ available: true, category: 'dim' })?.used_mib).toBeNull()
  })

  it('falls back to the dim category for an unknown value', () => {
    expect(toGpuInfo({ available: true, category: 'purple', total_mib: 100, used_mib: 50 })?.category).toBe('dim')
  })

  it('treats an empty name as unknown (null)', () => {
    expect(toGpuInfo({ available: true, category: 'dim', name: '' })?.name).toBeNull()
  })
})
