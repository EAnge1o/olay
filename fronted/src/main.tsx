import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import InteractionLayer from './InteractionLayer'
import './index.css'
import './interaction.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <InteractionLayer />
    <App />
  </React.StrictMode>,
)
