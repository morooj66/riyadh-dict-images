import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { initRuntimeConfig } from './api/client.ts'

// Fetch runtime config (API key) before rendering.
// Falls back gracefully if /config is unreachable.
initRuntimeConfig().finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
