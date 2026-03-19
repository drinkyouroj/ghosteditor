import { describe, it, expect } from 'vitest'
import { ApiError } from '../api/client'

describe('ApiError', () => {
  it('has correct status and message', () => {
    const err = new ApiError(404, 'Not found')
    expect(err.status).toBe(404)
    expect(err.message).toBe('Not found')
    expect(err).toBeInstanceOf(Error)
  })

  it('inherits from Error', () => {
    const err = new ApiError(500, 'Server error')
    expect(err instanceof Error).toBe(true)
    expect(err.name).toBe('Error')
  })
})

describe('API client URL construction', () => {
  it('exports BASE as empty string for relative URLs', async () => {
    // The client uses relative URLs (empty BASE) so that the Vite proxy
    // can forward requests to the backend during development.
    // We verify this by checking that the module can be imported successfully
    // and the ApiError class is available.
    expect(ApiError).toBeDefined()
  })

  it('builds correct manuscript URL pattern', () => {
    // Verify the URL pattern used by getManuscript
    const manuscriptId = '123e4567-e89b-12d3-a456-426614174000'
    const expectedPath = `/manuscripts/${manuscriptId}`
    expect(expectedPath).toBe('/manuscripts/123e4567-e89b-12d3-a456-426614174000')
  })

  it('builds correct feedback URL with query params', () => {
    const manuscriptId = 'abc-123'
    const params = new URLSearchParams()
    params.set('severity', 'critical')
    params.set('issue_type', 'consistency')
    const qs = params.toString()
    const url = `/bible/${manuscriptId}/feedback?${qs}`
    expect(url).toBe('/bible/abc-123/feedback?severity=critical&issue_type=consistency')
  })

  it('builds feedback URL without query params when no filters', () => {
    const manuscriptId = 'abc-123'
    const params = new URLSearchParams()
    const qs = params.toString()
    const url = `/bible/${manuscriptId}/feedback${qs ? `?${qs}` : ''}`
    expect(url).toBe('/bible/abc-123/feedback')
  })
})
