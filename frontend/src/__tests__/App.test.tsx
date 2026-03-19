import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { App } from '../App'

// Mock the API client so no real HTTP requests are made
vi.mock('../api/client', () => ({
  getMe: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  },
}))

import { getMe } from '../api/client'
const mockGetMe = vi.mocked(getMe)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('App', () => {
  it('renders without crashing', async () => {
    mockGetMe.mockRejectedValue(new Error('Not authenticated'))

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )

    // App starts with a loading spinner, then resolves
    await waitFor(() => {
      expect(document.body).toBeTruthy()
    })
  })

  it('shows the landing page for unauthenticated users', async () => {
    mockGetMe.mockRejectedValue(new Error('Not authenticated'))

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )

    // Wait for loading to complete and landing page to appear
    await waitFor(() => {
      expect(screen.getAllByText(/GhostEditor/i).length).toBeGreaterThan(0)
    })
  })
})
