# Compatibilidade e recuperação

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

A restauração é transacional e preserva o ambiente anterior se ocorrer uma falha. A desinstalação não remove `%LOCALAPPDATA%\DataMaskStudio` por padrão.
