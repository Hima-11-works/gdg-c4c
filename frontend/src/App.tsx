import { MapPage } from './components/MapPage'
import { MapUiProvider } from './state/MapUiProvider'

function App() {
  return (
    <MapUiProvider>
      <MapPage />
    </MapUiProvider>
  )
}

export default App
