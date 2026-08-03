# Data Mask Studio

Data Mask Studio é uma aplicação desktop local para anonimização reversível de dados em arquivos CSV. Ela permite:

- sugerir colunas sensíveis, prefixos e normalizações, sempre com confirmação do usuário;
- selecionar colunas;
- configurar prefixos e normalizações;
- gerar CSVs anonimizados;
- processar arquivos em lote;
- salvar perfis;
- consultar valores pelo cofre local criptografado;
- criar backups criptografados;
- recuperar o cofre, as chaves e os perfis.
- restaurar CSVs anonimizados usando o cofre local.
- verificar localmente a integridade das chaves, do cofre e dos perfis sem expor dados.

A versão atual permite restaurar seletivamente CSVs anonimizados com os mapeamentos do cofre local.
Ela também restaura códigos presentes em arquivos HTML e dashboards locais sem executar o conteúdo.

A aplicação atualmente é voltada ao Windows porque utiliza o Windows DPAPI para proteger as chaves locais.

## Tecnologias utilizadas

- Python 3.12+
- PySide6
- SQLite
- cryptography / AES-256-GCM
- Windows DPAPI
- pytest

## Como usar

### Opção recomendada

Baixe `DataMaskStudio-Setup-0.7.0.exe` na GitHub Release e siga as etapas do instalador. A desinstalação preserva o cofre e as chaves em `%LOCALAPPDATA%\DataMaskStudio`.

### Versão portátil

Baixe `DataMaskStudio-Portable-0.7.0.zip`, extraia a pasta completa e execute `DataMaskStudio.exe`. Não execute o programa diretamente de dentro do ZIP.

### Código-fonte

No Windows PowerShell:

```powershell
git clone https://github.com/Gustavo-Wallace/data-mask-studio.git
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
8. Na aba “Restaurar HTML”, analise e restaure códigos presentes em um dashboard local.

Perfis salvos também podem ser utilizados na aba de anonimização em lote.

A aba de backup permite gerar um arquivo `.dmsbackup` protegido por senha.

Na aba “Integridade”, a verificação local produz somente um relatório técnico seguro e não altera o cofre.

O programa é destinado atualmente ao Windows. Os executáveis da versão 0.7.0 ainda não possuem assinatura digital, portanto o Windows SmartScreen poderá apresentar um aviso de segurança.
