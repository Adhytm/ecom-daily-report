import { ref } from 'vue'

const STORAGE_KEY = 'ecom_theme_mode'
export const themeMode = ref(localStorage.getItem(STORAGE_KEY) || 'auto')
export const isDark = ref(false)

let mediaQuery = null

function updateDarkMode(dark) {
  isDark.value = dark
  document.documentElement.classList.toggle('dark', dark)
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
}

export function setTheme(mode) {
  themeMode.value = mode
  localStorage.setItem(STORAGE_KEY, mode)

  if (mode === 'auto') {
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
    updateDarkMode(Boolean(prefersDark))
  } else {
    updateDarkMode(mode === 'dark')
  }
}

export function initTheme() {
  if (typeof window === 'undefined') return

  mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
  const handleSystemChange = (e) => {
    if (themeMode.value === 'auto') {
      updateDarkMode(e.matches)
    }
  }

  if (mediaQuery.addEventListener) {
    mediaQuery.addEventListener('change', handleSystemChange)
  } else if (mediaQuery.addListener) {
    mediaQuery.addListener(handleSystemChange)
  }

  setTheme(themeMode.value)
}
