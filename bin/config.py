"""Configuração desta máquina: ~/.config/jarvis/local.env, que NÃO vai para o repositório.

Formato: CHAVE=valor, uma por linha; # comenta. Variável de ambiente com o mesmo nome ganha.
Aqui fica o que é da empresa ou só desta máquina (endereço do GitLab, usuário, projeto).
Sem o arquivo, cada script segue com o padrão dele e a parte que depende disso fica desligada.
Modelo comentado: config/local.env.exemplo, no repositório.
"""
import os

ARQUIVO = os.path.expanduser("~/.config/jarvis/local.env")


def _ler():
    valores = {}
    try:
        for linha in open(ARQUIVO, encoding="utf-8"):
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                chave, valor = linha.split("=", 1)
                valores[chave.strip()] = valor.strip().strip('"').strip("'")
    except OSError:
        pass
    return valores


_VALORES = _ler()


def get(chave, padrao=""):
    return os.environ.get(chave) or _VALORES.get(chave) or padrao
