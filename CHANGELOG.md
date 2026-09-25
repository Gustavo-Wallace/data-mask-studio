# Changelog

## 1.2.0 — Data Preparation Update

- Ações por coluna Preservar, Mascarar e Excluir.
- Normalização de colunas preservadas e renomeação de cabeçalhos de saída.
- Colunas compostas com ações Preservar ou Mascarar e normalização independente.
- Profile Format v2 com identidade mais rigorosa das colunas de origem.
- Suporte a colunas compostas no processamento em lote e na restauração.
- Inspeção e auditoria de integridade de mapeamentos compostos no cofre.
- Vault Schema 4 com migração compatível de versões anteriores suportadas.
- Reforços na proteção de chaves, concorrência e recuperação do ambiente.
- Publicação de saídas mais segura e recuperável.
- Restauração HTML sensível ao contexto.
- Leituras consistentes do cofre durante consultas e restauração.
- Validação mais rigorosa de colunas desconhecidas e arquivos temporários.

## 1.1.0

- Nova normalização `PERSON_NAME` para variações de caixa, diacríticos e whitespace.
- Recuperação determinística de cabeçalhos CSV vazios com nomes `column_<posição>` e resolução de colisões.
- Suporte de entrada a CSV UTF-16 LE/BE e UTF-32 LE/BE com BOM.
- Integração das novas capacidades aos fluxos individual, em lote e de restauração.
- Nenhuma alteração em criptografia, tokens, cofre, backups ou formatos persistentes.

## 1.0.4

- Otimização da análise e restauração de HTML comprovada por benchmarks sintéticos reproduzíveis.
- Consultas agrupadas ao cofre, conexão somente leitura reutilizada e cache limitado durante a restauração de HTML.
- README principal em inglês e README.pt-BR.md como versão oficial em português.
- Correção responsiva da captura de tela no GitHub Pages.
- Nenhuma alteração em criptografia, tokens, cofre, backups ou formatos persistentes.

## 1.0.3

- Tema visual escuro consistente independentemente do tema do Windows.
- Correção da abertura por clique dos links públicos na janela Sobre.
- Consolidação da indicação pública da licença GPL-3.0-only e do copyright do autor.
- Nenhuma alteração em criptografia, tokens, cofre, backups ou formatos persistentes.

## 1.0.2

- Correção da inspeção e anonimização de CSVs com uma única coluna.
- Diagnóstico seguro de linhas CSV com colunas faltantes ou excedentes.
- Reinicialização consistente do progresso na restauração HTML.
- Nenhuma alteração nos tokens, no cofre, no schema ou nos formatos existentes.

## 1.0.1

- Reformulação da apresentação do README e captura segura da interface.
- Correção dos links do diálogo Sobre.
- Template seguro para relatos de bugs.
- Pequenos ajustes no GitHub Actions.
- Nenhuma alteração nos tokens, no cofre ou nos formatos existentes.

## 1.0.0

- Primeiro lançamento estável.
- Identidade visual oficial DMS e informações públicas no diálogo Sobre.
- Consolidação de anonimização e restauração individual e em lote para CSV e HTML.
- Cofre local criptografado, backups, auditoria de integridade e manutenção.
- Contrato de compatibilidade para tokens, schema 3 e backups existentes.
- Distribuição portátil e instalador por usuário para Windows.

## Marcos anteriores

- 0.11.x: revisão da navegação, responsividade e acessibilidade da interface.
- 0.10.x: detecção assistida e otimizações para arquivos de alta cardinalidade.
- 0.9.x: integridade, manutenção e migração segura do cofre para schema 3.
- 0.8.x: restauração em lote e recuperação segura do ambiente.
- 0.5.x–0.7.x: distribuição Windows, restauração, backup e auditoria.
- 0.1.x–0.4.x: leitura de CSV, configuração de colunas, anonimização e cofre local.
