import { Suspense, lazy } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Shell } from './components/layout/Shell'
import { Involuntario } from './routes/Involuntario'
import { VisaoGeral } from './routes/VisaoGeral'
import { t } from './lib/idioma'

// As páginas menos visitadas carregam só quando abertas (o pacote inicial fica menor).
const Voluntario = lazy(() => import('./routes/Voluntario').then((m) => ({ default: m.Voluntario })))
const Simulacao = lazy(() => import('./routes/Simulacao').then((m) => ({ default: m.Simulacao })))
const Assistente = lazy(() => import('./routes/Assistente').then((m) => ({ default: m.Assistente })))
const PaginaApi = lazy(() => import('./routes/Api').then((m) => ({ default: m.PaginaApi })))
const Configuracao = lazy(() => import('./routes/Configuracao').then((m) => ({ default: m.Configuracao })))

function Carregando() {
  return <div className="card-glass min-h-[420px] rounded-[18px]" aria-busy="true" aria-label={t('Carregando a página')} />
}

export default function App() {
  return (
    <BrowserRouter>
      <Shell>
        <Suspense fallback={<Carregando />}>
          <Routes>
            <Route path="/" element={<VisaoGeral />} />
            <Route path="/involuntario" element={<Involuntario />} />
            <Route path="/voluntario" element={<Voluntario />} />
            <Route path="/simulacao" element={<Simulacao />} />
            <Route path="/assistente" element={<Assistente />} />
            <Route path="/api" element={<PaginaApi />} />
            <Route path="/configuracao" element={<Configuracao />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </Shell>
    </BrowserRouter>
  )
}
