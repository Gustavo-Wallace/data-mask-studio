# Privacidade

O Data Mask Studio processa arquivos localmente no computador do usuário. A aplicação não possui telemetria, conta de usuário, sincronização ou envio automático de arquivos, códigos, métricas ou diagnósticos.

O cofre, as chaves protegidas pelo Windows DPAPI, os perfis e as configurações permanecem em `%LOCALAPPDATA%\DataMaskStudio`. Arquivos anonimizados, restaurados, backups e pacotes de transferência são gravados somente nos destinos escolhidos pelo usuário.

Pacotes `.dmspackage` contêm mapeamentos sensíveis selecionados para o CSV vinculado, protegidos por senha; não são enviados automaticamente. Diferentemente de um backup completo, não incluem as chaves locais nem todos os mapeamentos do cofre.

Relatórios de integridade e manutenção evitam exibir valores originais, códigos completos, chaves e senhas. Antes de compartilhar qualquer diagnóstico, confirme que ele não foi combinado com dados reais por ferramentas externas.
