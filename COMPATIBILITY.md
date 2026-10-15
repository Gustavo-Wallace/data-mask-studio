# Compatibilidade e recuperação

## Versão 1.3.0

A versão 1.3.0 mantém o schema 4 do cofre, o Profile Format v2 (com leitura de perfis v1 válidos), o formato v1 de backup `.dmsbackup` e os formatos existentes de tokens. O `.dmspackage` tem formato v1 próprio, independente do schema do cofre e do formato de backup.

Cofres dos schemas 1, 2 e 3 continuam com migrações suportadas até o schema 4, desde que as chaves originais estejam disponíveis. Isso não garante que versões antigas consigam abrir um cofre já migrado. Backups com schemas suportados são verificados e migrados no ambiente de preparação antes da instalação; backups produzidos por versões do aplicativo posteriores à instalada são considerados incompatíveis.

## Restauração local, transferência e backup

- **Cofre local:** exige o banco e as chaves originais protegidas pelo Windows DPAPI. Copiar apenas o banco ou os arquivos DPAPI não substitui um backup portátil.
- **Transferência seletiva (`.dmspackage`):** exporta apenas os mapeamentos escalares e compostos necessários ao CSV gerado, não as chaves locais, todos os mapeamentos do cofre ou os perfis. O destinatário usa o CSV vinculado, o pacote, a senha e uma versão com suporte ao formato v1. Não há importação para o cofre do destinatário nem fallback para ele.
- **Backup do ambiente (`.dmsbackup`):** protege por senha as chaves locais, o cofre e os perfis quando presentes. Não inclui CSVs, HTMLs nem pacotes de transferência. Na recuperação, as chaves são novamente protegidas pelo DPAPI do ambiente de destino. Senha mínima de criação: 12 caracteres, contra 8 nos pacotes.

Pacotes e backups são sensíveis. Sem suas senhas não há recuperação remota; não os compartilhe publicamente como exemplos ou diagnósticos.

## Limites dos pacotes de transferência

A interface gera pacotes opcionalmente no mascaramento individual de CSV com ao menos uma ação Mascarar e os utiliza em Restaurar CSV. HTML e processamento em lote continuam usando o cofre local. Um pacote não permite restaurar outro CSV: o vínculo verifica o tamanho e o SHA-256 dos bytes, inclusive cabeçalhos, codificação e quebras de linha. Não edite o CSV entre geração e restauração. Senha incorreta, vínculo inválido ou mapeamento necessário ausente impedem a restauração; não há substituição silenciosa da fonte.

O formato preserva os valores originais e canônicos necessários às políticas de restauração existentes, não um histórico completo do cofre. Normalizações e composições não garantem reconstrução byte a byte de cada representação de entrada; colunas excluídas não são recuperadas pelo pacote.

O limite padrão é de 64 MiB de payload descriptografado, não de tamanho do CSV. O processamento do pacote ocorre em memória, e grandes conjuntos de valores únicos ou valores longos podem atingir esse limite. Chamadores da API podem definir um orçamento maior, até 1 GiB; a interface não oferece esse ajuste. Esses limites não são negociados pelo arquivo e não tornam o formato ilimitado.

## Contrato da versão 1.0.0

Atualizações compatíveis da série 1.0.x não devem alterar tokens ou códigos já gerados, nem impedir a abertura de cofres e backups válidos existentes. Permanecem estáveis o HMAC-SHA256, Base32 truncado, AES-256-GCM, AAD versionado, Windows DPAPI, schema 3, normalizações e fallback exato.

Cofres schema 2 são migrados automaticamente para schema 3 em operações que permitem escrita. A migração usa uma transação imediata, preserva códigos, valores, variações, ocorrências e datas, e executa rollback integral em caso de falha. A migração é idempotente.

Snapshots de manutenção e backup utilizam a API de backup do SQLite para incorporar alterações confirmadas no WAL. Diagnósticos somente leitura não classificam uma migração pendente como adulteração.

## Recuperação

1. Feche todas as instâncias do Data Mask Studio.
2. Abra a área Backup e recuperação.
3. Selecione um arquivo `.dmsbackup` criado pelo aplicativo e informe sua senha.
4. Revise o resumo antes de confirmar a restauração.
5. Após a restauração, execute a verificação de Integridade.

A restauração usa preparação, verificação e recuperação do ambiente anterior em caso de falha. Uma falha de recuperação exige revisão manual; não descarte as evidências nem presuma que o ambiente está pronto para uso. A validação do backup não substitui a autenticação dos registros realizada antes de instalar o ambiente restaurado. A desinstalação não remove `%LOCALAPPDATA%\DataMaskStudio` por padrão.

Snapshots consistentes usam a API de backup do SQLite, incluindo alterações confirmadas no WAL. Se a compactação e seu rollback falharem, o snapshot de segurança permanece em uma subpasta `dms-vault-compaction-*` do ambiente local para revisão manual. Esse snapshot não substitui um `.dmsbackup` e ainda depende das chaves locais originais.
