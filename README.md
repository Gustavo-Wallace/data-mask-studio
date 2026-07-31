# Data Mask Studio

Data Mask Studio é uma aplicação desktop local para anonimização reversível de dados em arquivos CSV. Ela permite:

- selecionar colunas;
- configurar prefixos e normalizações;
- gerar CSVs anonimizados;
- processar arquivos em lote;
- salvar perfis;
- consultar valores pelo cofre local criptografado;
- criar backups criptografados;
- recuperar o cofre, as chaves e os perfis.
- restaurar CSVs anonimizados usando o cofre local.

A versão atual permite restaurar seletivamente CSVs anonimizados com os mapeamentos do cofre local.

A aplicação atualmente é voltada ao Windows porque utiliza o Windows DPAPI para proteger as chaves locais.

## Tecnologias utilizadas

- Python 3.12+
- PySide6
- SQLite
- cryptography / AES-256-GCM
- Windows DPAPI
- pytest

## Como usar

No Windows PowerShell:

```powershell
git clone <URL_DO_REPOSITORIO>
cd data-mask-studio
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m data_mask_studio
```

Fluxo resumido:

1. Selecione um CSV.
2. Escolha as colunas que serão anonimizadas.
3. Configure prefixos e regras de normalização.
4. Valide a configuração.
5. Gere o CSV anonimizado.
6. Use o consultor local quando precisar recuperar um valor específico.
7. Na aba “Restaurar CSV”, escolha um arquivo anonimizado e as colunas que deseja restaurar.

Perfis salvos também podem ser utilizados na aba de anonimização em lote.

A aba de backup permite gerar um arquivo `.dmsbackup` protegido por senha.
