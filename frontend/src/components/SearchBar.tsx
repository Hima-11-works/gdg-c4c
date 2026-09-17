// Location search — states, districts, cities, localities. Client-side
// ranked search over the bundled GeoNames dataset (lib/locations.ts),
// lazy-loaded on first focus. Selecting a result dispatches FOCUS_LOCATION,
// which MapView turns into a flyTo.

import { useEffect, useMemo, useRef, useState } from 'react'
import { KIND_LABEL, ZOOM_BY_KIND, loadLocations, searchLocations } from '../lib/locations'
import { useMapUi } from '../state/MapUiContext'
import type { IndiaLocation } from '../lib/locations'

export function SearchBar() {
  const { dispatch } = useMapUi()
  const [query, setQuery] = useState('')
  const [locations, setLocations] = useState<IndiaLocation[] | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Lazy-load the dataset once, on first focus.
  const ensureLoaded = () => {
    if (locations !== null || loadError) return
    loadLocations()
      .then(setLocations)
      .catch(() => setLoadError(true))
  }

  const results = useMemo(
    () => (locations ? searchLocations(locations, query) : []),
    [locations, query],
  )

  // Close on outside click.
  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  const select = (loc: IndiaLocation) => {
    dispatch({
      type: 'FOCUS_LOCATION',
      latitude: loc.lat,
      longitude: loc.lon,
      zoom: ZOOM_BY_KIND[loc.t],
    })
    setQuery(loc.n)
    setOpen(false)
    setActiveIndex(-1)
    inputRef.current?.blur()
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (!open) setOpen(true)
      setActiveIndex((i) => Math.min(i + 1, results.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const chosen = results[activeIndex] ?? results[0]
      if (chosen) select(chosen)
    } else if (event.key === 'Escape') {
      setOpen(false)
      setActiveIndex(-1)
    }
  }

  const showDropdown = open && query.trim().length > 0

  return (
    <div className="panel search-bar" ref={containerRef}>
      <div className="search-input-row">
        <svg className="search-icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
          <line x1="16.5" y1="16.5" x2="21" y2="21" stroke="currentColor" strokeWidth="2" />
        </svg>
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={showDropdown}
          aria-controls={showDropdown ? 'location-listbox' : undefined}
          aria-autocomplete="list"
          aria-activedescendant={activeIndex >= 0 ? `location-option-${activeIndex}` : undefined}
          aria-label="Search states, districts, cities, and localities"
          placeholder="Search state, district, city, locality…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            setOpen(true)
            setActiveIndex(-1)
          }}
          onFocus={() => {
            ensureLoaded()
            setOpen(true)
          }}
          onKeyDown={onKeyDown}
        />
        {query.length > 0 && (
          <button
            type="button"
            className="search-clear"
            aria-label="Clear search"
            onClick={() => {
              setQuery('')
              setActiveIndex(-1)
              inputRef.current?.focus()
            }}
          >
            ✕
          </button>
        )}
      </div>

      {showDropdown && (
        <ul className="search-results" role="listbox" id="location-listbox">
          {loadError && <li className="search-empty">Couldn't load locations.</li>}
          {!loadError && locations === null && <li className="search-empty">Loading…</li>}
          {!loadError && locations !== null && results.length === 0 && (
            <li className="search-empty">No matching locations.</li>
          )}
          {results.map((loc, index) => (
            <li key={`${loc.t}-${loc.n}-${loc.lat}-${loc.lon}`} role="presentation">
              <button
                type="button"
                id={`location-option-${index}`}
                role="option"
                aria-selected={index === activeIndex}
                className={`search-result ${index === activeIndex ? 'active' : ''}`}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => select(loc)}
              >
                <span className="search-result-name">{loc.n}</span>
                <span className="search-result-meta">
                  {KIND_LABEL[loc.t]}
                  {loc.t !== 'state' && loc.s ? ` · ${loc.s}` : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
