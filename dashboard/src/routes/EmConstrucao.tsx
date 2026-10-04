import { Card } from '../components/ui/Card'

export function EmConstrucao({ titulo, descricao }: { titulo: string; descricao: string }) {
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="t-h2 text-paper">{titulo}</h2>
        <p className="t-apoio mt-1 text-silver">{descricao}</p>
      </div>
      <Card className="flex min-h-[320px] items-center justify-center p-8 text-center">
        <div>
          <div className="t-h3 text-paper">Esta página entra na próxima etapa</div>
          <p className="t-apoio mt-2 max-w-md text-silver">O desenho está no plano; a página é construída depois da aprovação do visual do involuntário.</p>
        </div>
      </Card>
    </div>
  )
}
