# Data Mask Studio

![Ícone do Data Mask Studio](assets/branding/dms_icon_1024.png)

Data Mask Studio é uma aplicação desktop local para anonimização reversível de dados em arquivos CSV. A versão 1.0.0 consolida o primeiro lançamento estável para Windows.

A aplicação permite selecionar colunas, configurar prefixos e normalizações, gerar e restaurar CSVs anonimizados, restaurar códigos em HTML, processar arquivos em lote, salvar perfis, consultar o cofre local criptografado, criar backups e executar verificações de integridade e manutenção.

A versão atual permite restaurar seletivamente CSVs anonimizados com os mapeamentos do cofre local. Ela também restaura códigos presentes em arquivos HTML e dashboards locais sem executar o conteúdo.

Todo o processamento ocorre localmente, sem telemetria ou envio automático de dados. As chaves locais são protegidas pelo Windows DPAPI, por isso a distribuição atual é destinada ao Windows.

## Instalação e uso

### Instalador

Baixe `DataMaskStudio-Setup-1.0.0.exe` na GitHub Release e execute o instalador. A instalação é feita por usuário e a desinstalação preserva por padrão o ambiente em `%LOCALAPPDATA%\DataMaskStudio`.

### Versão portátil

Baixe `DataMaskStudio-Portable-1.0.0.zip`, extraia a pasta completa e execute `DataMaskStudio.exe`. Não execute o programa diretamente de dentro do ZIP.

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

Fluxo básico:

1. Selecione um CSV e escolha as colunas que serão anonimizadas.
2. Configure prefixos e normalizações e valide a configuração.
3. Gere o CSV anonimizado ou utilize um perfil no processamento em lote.
4. Para recuperar dados tabulares, use a aba “Restaurar CSV”.
5. Para dashboards locais, use a aba “Restaurar HTML”; lotes possuem uma área própria.
6. Consulte códigos específicos pelo consultor local quando necessário.

## Backup, integridade e manutenção

Backups `.dmsbackup` são criptografados com uma senha informada pelo usuário. A restauração preserva a consistência do cofre, das chaves e dos perfis.

As áreas de Integridade e Cofre e manutenção produzem relatórios técnicos seguros, validam backups, localizam temporários e permitem compactação controlada do banco.

## Compatibilidade

A versão 1.0.0 usa o schema 3 do cofre e mantém os formatos de tokens, códigos e backups das versões anteriores. Cofres antigos suportados são migrados de forma transacional. Consulte [COMPATIBILITY.md](COMPATIBILITY.md) para o contrato de estabilidade e orientações de recuperação.

Atualizações compatíveis não devem alterar códigos já gerados nem impedir a abertura de cofres e backups existentes.

## Limitações

- O aplicativo é destinado ao Windows por depender do Windows DPAPI.
- Os executáveis ainda não possuem assinatura digital e podem acionar um aviso do Windows SmartScreen.
- O Windows pode manter ícones antigos em cache; valide o novo ícone com uma instalação limpa ou um novo atalho.
- Não existe sincronização, telemetria ou recuperação remota de chaves.

## Tecnologias utilizadas

- Python 3.12+
- PySide6
- SQLite
- cryptography / AES-256-GCM
- Windows DPAPI
- pytest
