/**
 * CSV para abrir no Excel em português: separador ";" e vírgula decimal,
 * com BOM para os acentos saírem certos.
 */
export function baixarCsv(nome: string, cabecalho: string[], linhas: (string | number)[][]) {
  const celula = (v: string | number) => {
    const s = typeof v === 'number' ? v.toFixed(2).replace('.', ',') : v
    return /[";\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const texto = [cabecalho, ...linhas].map((l) => l.map(celula).join(';')).join('\r\n')
  const blob = new Blob(['﻿' + texto], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = nome
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
