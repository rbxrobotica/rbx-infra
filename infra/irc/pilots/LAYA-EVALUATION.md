# Laya: ensaio local de triagem em português

Resultado de 2026-10-11: **esta configuração não justificou substituir as regras**.
O ensaio usou 32 mensagens sintéticas, oito por classe, sem tráfego IRC, documentos
privados, chamadas a provedores, instalação global ou efeito de autorização.

| Medida | Regras fixas | Laya multilingual |
| --- | ---: | ---: |
| Acertos | 29/32 | 8/32 |
| Acurácia descritiva | 90,625% | 25% |
| Macro-F1 | 0,9051 | 0,2056 |
| Pedidos `out_of_scope` classificados como documental/análise | 1 | 1 |

O Laya escolheu `clarification` em 23 casos. Com o limiar **pré-declarado**, não
calibrado, de probabilidade 0,8, aceitou oito casos e acertou três. Exemplos de
erro confiante: D01, uma definição de Action, virou `out_of_scope` com 0,9359;
A05, uma proposta de critérios, virou `clarification` com 0,9553; O06, que pede
explicação e implantação, virou `clarification` com 0,9073. O limiar não corrigiu
a baixa qualidade desta configuração.

São rótulos provisórios de um único autor, sem adjudicação humana independente.
Os casos têm pistas lexicais simples que favorecem o baseline. Não houve ajuste
de regras, prompt, pesos ou limiar após observar as previsões, mas esta amostra
pequena e sintética não estima a qualidade em produção nem a capacidade geral
do modelo. Novos ajustes exigiriam outro conjunto de teste independente.

O alias explícito foi reconhecido corretamente nos 32 casos de sintaxe: 26
destinos `flightdeck-dev` e seis ausências/menções não admitidas. Isso requer
**zero inferências**. Sintaxe não prova conta autenticada, canal, atualidade,
sessão ou permissão. O erro Laya `out_of_scope`→documental ocorreu em O08, que
tem alias desconhecido e seria recusado antes do atendimento real. Classificação
e probabilidade não concedem autorização, aprovação ou acesso a segredos.

## Execução e medidas

- Python 3.12.3; Torch **2.14.1+cpu**, Transformers 5.19.0; backend eager,
  FP32, duas threads, lote 1, seed 20261011, timeout de 300 segundos.
- Um processo, uma carga, primeira passagem de 32 casos seguida de uma passagem
  quente dos mesmos 32. Nenhum rótulo mudou entre as passagens. Sem GPU.
- Carga do modelo: **3,804 s**, além de **0,705 s** de importação. Primeira
  inferência: **221,4 ms**; p50 quente **221,5 ms**; p95 quente **230,9 ms**.
- RSS máximo do processo: **2.385.424 KiB (~2,275 GiB)**. Tempo total **19,07 s**,
  CPU acumulada **33,26 s**. Não houve flush do page cache do sistema; “primeira”
  não significa leitura de disco frio. Medidas pertencem a esta execução.
- Arquivos de modelo: **678.201.614 bytes**, download verificado em **11,12 s**.
  Ambiente instalado: **1.128.686.978 bytes lógicos**, sem somar caches ou cópia
  temporária do modelo. Wheels, dependências transitivas e pesos foram fixados
  e verificados por SHA-256. O download e a instalação ficam fora do tempo de
  inferência. Não foi feito treinamento ou calibração.
- Namespace de rede Linux separado (`unshare --user --map-root-user --net`),
  ambiente sanitizado, HF/Transformers offline, token implícito desabilitado e
  guarda suplementar de sockets Python. Sem acesso à rede durante inferência.

Uma auditoria posterior **somente de tokenização**, sem nova inferência, descartou
truncamento: 86/86 tokens de instruções preservados, head de 173 tokens, quatro
opções distintas de 18/19/18/25 tokens, zero tokens de estado descartados nos 32
casos. O resultado negativo refere-se ao prompt, checkpoint e runtime fixados;
não há evidência de que ocorreu por corte de entrada.

## Artefatos e proveniência

- [Casos congelados](laya_eval_cases.json),
  [harness](laya_eval.py), [manifesto completo](laya-runtime.json),
  [resultados por caso, matriz e medidas](evidence/laya-evaluation.json).
- Código: [Laya v0.4.3, commit c4a805b](https://github.com/NandhaKishorM/laya/tree/c4a805b5160621bbfc53417126990a67d3163bc5).
  Sete arquivos do caminho de carga/inferência do wheel instalado foram
  comparados byte a byte com esse commit; o manifesto guarda os hashes.
  Isso não é uma auditoria integral das dependências.
- Modelo: [convaiinnovations/laya-multilingual, e4e9ddf](https://huggingface.co/convaiinnovations/laya-multilingual/tree/e4e9ddf21a7b1903b7acffd8814ad4307bf63a67),
  pin revisado pelo upstream, com cinco arquivos explicitamente permitidos.
  Pesos Safetensors; não se carregou pickle ou código remoto do checkpoint.
- O checkpoint sai com temperaturas 1,0 e sem calibração ajustada. O campo
  `confidence` baseado em entropia difere de `answer_confidence`, usado no limiar.
  Os dois estão sujeitos a erro; ver [limites oficiais](https://huggingface.co/convaiinnovations/laya-multilingual).

O SDK normaliza `tokenizer_config.json` durante a carga. O harness usa uma cópia
temporária para preservar os bytes originais verificados e registra o hash do
JSON normalizado. Somente código, casos, manifesto e evidência entram no Git;
venv, wheels, pesos e caches do ensaio são descartáveis.

## Reprodução isolada

Pré-requisitos: Linux x86_64 com `/usr/bin/python3` **3.12.3**, `unshare` com user
namespace permitido, acesso público a PyPI/PyTorch/Hugging Face para preparação
e alguns GiB livres. Este manifesto fixa wheels desta plataforma. O script
abaixo falha se o isolamento de rede não funcionar, se um hash divergir ou se
um pacote não estiver disponível. Não utiliza pip ou instalações globais.

Execute da raiz deste checkout. Os downloads precedem o processo sem rede.
O resultado reproduzido fica em `/tmp`; não sobrescreve a evidência revisada.

```bash
python3 - <<'PY'
import hashlib, json, os, pathlib, platform, shutil, subprocess, tempfile, urllib.request

assert platform.python_version() == "3.12.3"
work = pathlib.Path("infra/irc/pilots").resolve()
m = json.loads((work / "laya-runtime.json").read_text())
root = pathlib.Path(tempfile.mkdtemp(prefix="rbx-laya-pilot-"))
env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
       "PIP_CONFIG_FILE": "/dev/null", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
       "PIP_CACHE_DIR": str(root / "pip-cache"), "PYTHONNOUSERSITE": "1"}

def run(command, timeout=300, environment=env):
    subprocess.run(command, env=environment, check=True, timeout=timeout)

def fetch(url, destination, expected):
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    assert digest.hexdigest() == expected, str(destination)

run(["/usr/bin/unshare", "--user", "--map-root-user", "--net", "/usr/bin/true"])
run(["/usr/bin/python3", "-m", "venv", "--without-pip", str(root / "venv")])
python = str(root / "venv/bin/python")
pip = root / "pip-bootstrap.whl"
fetch(m["bootstrap_pip"]["url"], pip, m["bootstrap_pip"]["sha256"])
lock = root / "requirements.lock"
lock.write_text("\n".join(
    f'{p["name"]} @ {p["url"]} --hash=sha256:{p["sha256"]}'
    for p in m["packages"]) + "\n")
run([python, str(pip) + "/pip", "install", "--only-binary=:all:",
     "--require-hashes", "--no-deps", "--no-index", "-r", str(lock)])
for name, digest in m["model"]["sha256"].items():
    url = f'https://huggingface.co/{m["model"]["id"]}/resolve/{m["model"]["revision"]}/{name}'
    fetch(url, root / "model" / name, digest)

offline = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
    "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
    "USE_TF": "0", "USE_TORCH": "1", "CUDA_VISIBLE_DEVICES": "",
    "TOKENIZERS_PARALLELISM": "false", "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "DO_NOT_TRACK": "1",
    "HF_HOME": str(root / "run/hf-home"), "RBX_OFFLINE_NETNS": "1"}
result = root / "laya-evaluation.json"
run(["/usr/bin/unshare", "--user", "--map-root-user", "--net", python, "-I",
     str(work / "laya_eval.py"), "--model-dir", str(root / "model"),
     "--scratch-dir", str(root / "run"), "--manifest", str(work / "laya-runtime.json"),
     "--output", str(result), "--timeout-seconds", "300"], 320, offline)
print("Resultado reproduzido:", result)
print("Diretório temporário descartável:", root)
PY
```

O harness grava hashes dos próprios arquivos, versões instaladas e medidas. A
auditoria de tokenização adicional na evidência original usou `build_head` e
`build_sequence(..., return_stats=True, return_truncation_stats=True)` do mesmo
SDK sobre os textos/prompt preservados; não alterou o resultado do classificador.
Depois de preservar o resultado desejado, remova somente o diretório temporário
exato impresso pelo script.
