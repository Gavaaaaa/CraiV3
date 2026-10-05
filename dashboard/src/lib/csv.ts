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

/** A marca de UTF-8 no começo do arquivo: sem ela o Excel troca os acentos. */
const MARCA_DE_UTF8 = String.fromCharCode(0xfeff)

/**
 * Entrega para baixar um CSV que já veio pronto (do backend). O navegador tira a marca de UTF-8
 * quando lê a resposta como texto; ela volta aqui, uma vez só.
 */
export function baixarCsvPronto(nome: string, conteudo: string) {
  const texto = conteudo.startsWith(MARCA_DE_UTF8) ? conteudo : MARCA_DE_UTF8 + conteudo
  const blob = new Blob([texto], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = nome
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
