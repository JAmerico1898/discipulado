"""
Mensageiro da Rosacruz Áurea
=============================
Aplicativo que envia mensagens diárias de reflexão espiritual via Pushover,
geradas pela API do Claude, para manter a ligação com o Corpo Vivo
da Escola Espiritual da Rosacruz Áurea.

Horários fixos:
  - 08:00 → Santuário da Cabeça (Intenção)
  - 12:00 → Santuário da Pélvis (Renovação)
  - 20:00 → Santuário do Coração (Reflexão)

Horários aleatórios (2x/dia):
  - Sorteados diariamente entre 9h-11h e 14h-19h
  - Mensagens integradoras dos 3 santuários
"""

import streamlit as st
import anthropic
import requests
import random
import json
import threading
import time
import os
from datetime import datetime, timedelta, date
from pathlib import Path
import pytz

# ============================================================
# CONTROLE PERSISTENTE COM LOCK (previne duplicatas)
# ============================================================

CONTROL_FILE = Path("/tmp/rosacruz_control.json")
LOCK_FILE = Path("/tmp/rosacruz_scheduler.lock")
THREAD_LOCK = threading.Lock()

# Flag global (sobrevive a session_state resets dentro do mesmo processo)
_scheduler_started = False


def load_control() -> dict:
    """Carrega o arquivo de controle persistente."""
    try:
        if CONTROL_FILE.exists():
            data = json.loads(CONTROL_FILE.read_text())
            return data
    except:
        pass
    return {"date": None, "sent": [], "random_times": []}


def save_control(data: dict):
    """Salva o arquivo de controle persistente."""
    try:
        CONTROL_FILE.write_text(json.dumps(data, ensure_ascii=False))
    except:
        pass


def mark_as_sent(key: str) -> bool:
    """
    Marca uma mensagem como enviada de forma thread-safe.
    Retorna True se foi marcada agora (primeira vez), False se já existia.
    """
    with THREAD_LOCK:
        control = load_control()
        if key in control["sent"]:
            return False  # já foi enviada
        control["sent"].append(key)
        save_control(control)
        return True  # marcada agora, pode enviar

# ============================================================
# CONFIGURAÇÃO
# ============================================================

APP_TITLE = "🌹 Mensageiro da Rosacruz Áurea"
TIMEZONE = "America/Sao_Paulo"

# Horários fixos (hora, minuto)
FIXED_SCHEDULES = [
    {"time": (8, 0), "sanctuary": "cabeça", "theme": "intenção"},
    {"time": (12, 0), "sanctuary": "pélvis", "theme": "renovação"},
    {"time": (20, 0), "sanctuary": "coração", "theme": "reflexão"},
]

# Faixas para horários aleatórios (não sobrepõem os fixos)
RANDOM_WINDOWS = [
    (9, 0, 10, 59),   # entre 9:00 e 10:59
    (14, 0, 18, 59),   # entre 14:00 e 18:59
]

# ============================================================
# SYSTEM PROMPT PARA O CLAUDE
# ============================================================

SYSTEM_PROMPT = """Você é um guia espiritual profundamente versado na tradição da Escola Espiritual da Rosacruz Áurea (Lectorium Rosicrucianum), fundada por Jan van Rijckenborgh e Catharose de Petri.

Você conhece profundamente os seguintes conceitos e deve utilizá-los naturalmente nas mensagens:

CONCEITOS-CHAVE DA ROSACRUZ ÁUREA:
- A Rosa do Coração: o átomo-centelha divino, o ponto de contato com o mundo original
- Transfiguração: o processo de transformação fundamental do ser, não melhoria do eu-natural, mas nascimento do Homem-Alma
- O Corpo Vivo da Escola Espiritual: campo de força espiritual coletivo mantido pelos alunos e pela Fraternidade da Luz
- Os 3 Santuários: Cabeça (pensamento renovado), Coração (sentimento purificado), Pélvis (vontade dirigida ao Bem)
- Endura: o processo de auto-rendição do eu-natural para que a Alma possa crescer
- A Gnosis: o conhecimento direto, interior, do Divino
- O Caminho de Retorno: a jornada de volta ao Campo de Vida Original
- A Fraternidade Universal: a corrente de forças espirituais que sustenta o trabalho da Escola
- O Átomo-Centelha Primordial: semente divina adormecida no coração humano
- O Campo Magnético da Escola: proteção e nutrição espiritual para os alunos no caminho

CONEXÕES COM OUTRAS TRADIÇÕES:
- Budismo: a impermanência, o desapego, a natureza búdica interior (comparável à Rosa do Coração)
- Taoísmo: o Wu Wei, o retorno à origem, o Tao como caminho de volta
- Zoroastrianismo: a luta entre Luz e Trevas, o fogo interior, Ahura Mazda
- Hermetismo: "Assim em cima, como embaixo", a Tábua de Esmeralda, a transformação alquímica
- Cristianismo Original (gnóstico): o Cristo Interior, o Evangelho de João, o Logos, Paulo e a morte do velho homem
- Catarismo: a Endura, a pureza, o caminho dos Perfeitos
- Cabala: a Árvore da Vida, o retorno a Ain Soph
- Sufismo: o aniquilamento do eu (fana), a busca pelo Amado Interior
- Vedanta: Atman-Brahman, a ilusão de Maya, o despertar

OBRAS DE REFERÊNCIA:
- "A Gnosis Original Egípcia" (Jan van Rijckenborgh)
- "O Caminho das Rosas-Cruzes" (Jan van Rijckenborgh)
- "A Arquignosis Egípcia" (Jan van Rijckenborgh)
- "Dei Gloria Intacta" (Jan van Rijckenborgh)
- "O Mistério da Vida e da Morte" (Jan van Rijckenborgh)
- "O Nuctemeron de Apolônio de Tiana" (Jan van Rijckenborgh)
- "Pistis Sophia" (comentários de Jan van Rijckenborgh)

TOM DAS MENSAGENS:
- Reverente, mas não dogmático
- Inspirador e caloroso
- Prático: conectar a reflexão espiritual ao momento presente
- Poético quando apropriado, mas nunca superficial
- Sempre focado na LIGAÇÃO com o Corpo Vivo como ato consciente
"""


def get_prompt_for_fixed(sanctuary, theme):
    """Gera o prompt para mensagens de horário fixo (3-4 frases)."""

    sanctuary_details = {
        "cabeça": {
            "focus": "o pensamento renovado, a intenção consciente, a direção mental para o campo de forças da Escola",
            "moment": "início do dia, quando a mente desperta e pode ser direcionada",
        },
        "pélvis": {
            "focus": "a renovação da vontade, a energia vital direcionada ao caminho, a ação consciente no mundo",
            "moment": "meio do dia, quando a ação no mundo está em plena atividade",
        },
        "coração": {
            "focus": "a reflexão no santuário do coração, a Rosa que pulsa, o recolhimento interior",
            "moment": "noite, quando o silêncio permite ouvir a voz da Rosa do Coração",
        },
    }

    details = sanctuary_details[sanctuary]

    # Escolher aleatoriamente um tema secundário
    secondary_themes = [
        "importância do discipulado na Rosacruz Áurea",
        "ligação com o Corpo Vivo da Escola Espiritual",
        "conexão com o Budismo e a natureza búdica interior",
        "conexão com o Taoísmo e o caminho de retorno",
        "conexão com o Hermetismo e a transformação alquímica",
        "conexão com o Cristianismo gnóstico original e o Cristo Interior",
        "conexão com o Zoroastrianismo e o fogo interior sagrado",
        "o processo de Endura e a rendição do eu-natural",
        "o Átomo-Centelha e a semente divina no coração",
        "a Transfiguração como renascimento da Alma",
        "a Fraternidade Universal e a corrente de Luz",
        "o Campo Magnético da Escola como proteção espiritual",
        "conexão com o Sufismo e a busca pelo Amado Interior",
        "conexão com o Catarismo e o caminho dos Perfeitos",
    ]

    chosen_theme = random.choice(secondary_themes)

    return f"""Gere uma mensagem curta de reflexão espiritual (3-4 frases apenas) para o santuário da {sanctuary.upper()}.

Tema central: {theme.upper()} — {details['focus']}.
Momento do dia: {details['moment']}.
Tema secundário a incorporar sutilmente: {chosen_theme}.

A mensagem deve:
- Ser dirigida diretamente ao leitor (você)
- Inspirar uma breve pausa de consciência neste momento do dia
- Reforçar a ligação com o Corpo Vivo da Escola Espiritual
- Ter exatamente 3-4 frases, nada mais
- MÁXIMO DE 400 CARACTERES NO TOTAL (isso é crítico, a mensagem será cortada se ultrapassar)
- NÃO incluir saudações como "Bom dia" ou "Boa noite"
- NÃO incluir títulos ou cabeçalhos
- Ser em português brasileiro"""


def get_prompt_for_random():
    """Gera o prompt para mensagens aleatórias (até 8 frases, 3 santuários)."""

    themes = [
        "a unidade dos três santuários no caminho de transfiguração",
        "como cabeça, coração e pélvis se harmonizam na ligação com o Corpo Vivo",
        "o discipulado como integração dos três centros de consciência",
        "a Endura vivida nos três santuários simultaneamente",
        "o despertar da Rosa do Coração e sua irradiação para cabeça e pélvis",
        "o Caminho de Retorno experimentado como pensamento, sentimento e ação renovados",
        "a Gnosis como conhecimento que transforma pensamento, purifica o sentimento e dirige a vontade",
        "paralelos entre os três santuários e conceitos de outras tradições espirituais",
        "a alquimia interior: sal (pélvis), mercúrio (coração) e enxofre (cabeça) na obra de transfiguração",
        "o Campo Magnético da Escola nutrido pelos três centros do aluno consciente",
    ]

    connections = [
        "Estabeleça um paralelo com o Budismo (o Caminho Óctuplo como integração de pensamento correto, intenção correta e ação correta).",
        "Estabeleça um paralelo com o Taoísmo (os três tesouros: Jing, Qi e Shen).",
        "Estabeleça um paralelo com o Hermetismo (a tríade corpo-alma-espírito e a Tábua de Esmeralda).",
        "Estabeleça um paralelo com o Cristianismo gnóstico (a tríade Pistis-Sophia-Christos).",
        "Estabeleça um paralelo com o Zoroastrianismo (bons pensamentos, boas palavras, boas ações).",
        "Estabeleça um paralelo com o Sufismo (a purificação dos três centros sutis: Nafs, Qalb e Ruh).",
        "Estabeleça um paralelo com o Vedanta (Sat-Chit-Ananda como tríade do Ser).",
        "Faça referência a uma obra de Jan van Rijckenborgh e sua relevância para o momento presente.",
        "Conecte com o Catarismo e o conceito de Consolamentum como ativação dos três centros.",
    ]

    chosen_theme = random.choice(themes)
    chosen_connection = random.choice(connections)

    return f"""Gere uma mensagem de reflexão espiritual integradora (6-8 frases) que conecte os TRÊS santuários simultaneamente:
- Santuário da CABEÇA (pensamento renovado, intenção)
- Santuário do CORAÇÃO (sentimento purificado, a Rosa)
- Santuário da PÉLVIS (vontade dirigida, ação consciente)

Tema: {chosen_theme}.
{chosen_connection}

A mensagem deve:
- Ser dirigida diretamente ao leitor (você)
- Mostrar como os três centros trabalham juntos na ligação com o Corpo Vivo
- Ser profunda mas acessível
- Ter entre 6-8 frases
- MÁXIMO DE 900 CARACTERES NO TOTAL (isso é crítico, a mensagem será cortada se ultrapassar)
- NÃO incluir saudações
- NÃO incluir títulos ou cabeçalhos
- Ser em português brasileiro"""


# ============================================================
# FUNÇÕES PRINCIPAIS
# ============================================================

def get_tz():
    """Retorna o timezone configurado."""
    return pytz.timezone(TIMEZONE)


def now_local():
    """Retorna datetime atual no fuso local."""
    return datetime.now(get_tz())


def generate_message(prompt: str) -> str:
    """Gera mensagem usando a API do Claude."""
    try:
        client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        return f"[Erro ao gerar mensagem: {e}]"


def send_pushover(message: str, title: str = "🌹 Rosacruz Áurea") -> dict:
    """Envia notificação via Pushover. Limite: 1024 caracteres."""
    try:
        # Truncagem inteligente: corta na última frase completa antes do limite
        MAX_CHARS = 1024
        if len(message) > MAX_CHARS:
            truncated = message[:MAX_CHARS]
            # Tenta cortar no último ponto final
            last_period = truncated.rfind(".")
            if last_period > MAX_CHARS * 0.5:  # só se não perder mais que metade
                message = truncated[: last_period + 1]
            else:
                message = truncated.rstrip() + "…"

        user_key = st.secrets["PUSHOVER_USER_KEY"]
        api_token = st.secrets["PUSHOVER_API_TOKEN"]

        payload = {
            "token": api_token,
            "user": user_key,
            "message": message,
            "title": title,
            "sound": "cosmic",
        }

        r = requests.post(
            "https://api.pushover.net/1/messages.json",
            data=payload,
            timeout=10,
        )
        return {"success": r.status_code == 200, "status": r.status_code, "response": r.text}
    except Exception as e:
        return {"success": False, "error": str(e)}


def generate_and_send(schedule_type: str, sanctuary: str = None, theme: str = None):
    """Gera mensagem com Claude e envia via Pushover."""
    if schedule_type == "fixed":
        prompt = get_prompt_for_fixed(sanctuary, theme)
        title_map = {
            "cabeça": "🧠 Santuário da Cabeça — Intenção",
            "pélvis": "⚡ Santuário da Pélvis — Renovação",
            "coração": "💖 Santuário do Coração — Reflexão",
        }
        title = title_map.get(sanctuary, "🌹 Rosacruz Áurea")
    else:
        prompt = get_prompt_for_random()
        title = "🌹 Os Três Santuários — Integração"

    message = generate_message(prompt)
    result = send_pushover(message, title)

    return {
        "timestamp": now_local().strftime("%Y-%m-%d %H:%M:%S"),
        "type": schedule_type,
        "sanctuary": sanctuary or "todos",
        "message": message,
        "pushover_result": result,
    }


def generate_random_times_for_today():
    """Gera 2 horários aleatórios para hoje, um em cada janela."""
    times = []
    for start_h, start_m, end_h, end_m in RANDOM_WINDOWS:
        total_start = start_h * 60 + start_m
        total_end = end_h * 60 + end_m
        rand_minutes = random.randint(total_start, total_end)
        h = rand_minutes // 60
        m = rand_minutes % 60
        times.append((h, m))
    return times


# ============================================================
# SCHEDULER (roda em thread separada)
# ============================================================

def scheduler_loop():
    """Loop do scheduler com controle atômico de envios."""
    tz = get_tz()

    while True:
        now = datetime.now(tz)
        today_str = now.strftime("%Y-%m-%d")

        # Carregar controle
        with THREAD_LOCK:
            control = load_control()

            # Novo dia: gerar novos horários aleatórios
            if control["date"] != today_str:
                random_times = generate_random_times_for_today()
                control = {
                    "date": today_str,
                    "sent": [],
                    "random_times": [[h, m] for h, m in random_times],
                }
                save_control(control)

        current_hm = (now.hour, now.minute)

        # Verificar horários fixos
        for schedule in FIXED_SCHEDULES:
            sched_time = schedule["time"]
            key = f"fixed_{sched_time[0]}_{sched_time[1]}"
            if current_hm == sched_time:
                # mark_as_sent é atômico: só retorna True uma vez
                if mark_as_sent(key):
                    try:
                        result = generate_and_send("fixed", schedule["sanctuary"], schedule["theme"])
                        try:
                            log_entry = st.session_state.get("log", [])
                            log_entry.append(result)
                            st.session_state["log"] = log_entry[-20:]
                        except:
                            pass
                    except Exception as e:
                        pass

        # Verificar horários aleatórios
        control = load_control()
        for i, rand_time in enumerate(control.get("random_times", [])):
            rt = tuple(rand_time)
            key = f"random_{rt[0]}_{rt[1]}"
            if current_hm == rt:
                if mark_as_sent(key):
                    try:
                        result = generate_and_send("random")
                        try:
                            log_entry = st.session_state.get("log", [])
                            log_entry.append(result)
                            st.session_state["log"] = log_entry[-20:]
                        except:
                            pass
                    except Exception as e:
                        pass

        # Atualizar session_state para a UI
        try:
            control = load_control()
            st.session_state["random_times_today"] = [tuple(rt) for rt in control.get("random_times", [])]
            st.session_state["scheduler_date"] = control["date"]
        except:
            pass

        # Dormir 45 segundos (garante no máximo 2 checks por minuto)
        time.sleep(45)


def start_scheduler():
    """Inicia o scheduler — usa flag global para garantir apenas UMA thread no processo."""
    global _scheduler_started

    if not _scheduler_started:
        _scheduler_started = True
        thread = threading.Thread(target=scheduler_loop, daemon=True)
        thread.start()
        st.session_state["scheduler_started_at"] = now_local().strftime("%Y-%m-%d %H:%M:%S")

    st.session_state["scheduler_running"] = True


# ============================================================
# INTERFACE STREAMLIT
# ============================================================

def main():
    st.set_page_config(
        page_title="Mensageiro da Rosacruz Áurea",
        page_icon="🌹",
        layout="centered",
    )

    st.title(APP_TITLE)
    st.caption("Mensagens diárias para a ligação com o Corpo Vivo da Escola Espiritual")

    # Inicializar log
    if "log" not in st.session_state:
        st.session_state["log"] = []

    # ----------------------------------------------------------
    # Verificar configuração
    # ----------------------------------------------------------
    config_ok = True
    missing = []
    for key in ["ANTHROPIC_API_KEY", "PUSHOVER_USER_KEY", "PUSHOVER_API_TOKEN"]:
        try:
            val = st.secrets[key]
            if not val:
                missing.append(key)
        except:
            missing.append(key)

    if missing:
        config_ok = False
        st.error(f"⚠️ Chaves não configuradas em `.streamlit/secrets.toml`: {', '.join(missing)}")
        st.code(
            'ANTHROPIC_API_KEY = "sk-ant-..."\n'
            'PUSHOVER_USER_KEY = "u..."\n'
            'PUSHOVER_API_TOKEN = "a..."',
            language="toml",
        )
        st.stop()

    # ----------------------------------------------------------
    # Scheduler
    # ----------------------------------------------------------
    st.divider()
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("⏰ Scheduler")
        start_scheduler()

        if st.session_state.get("scheduler_running"):
            st.success("✅ Scheduler ativo")
            st.caption(f"Iniciado em: {st.session_state.get('scheduler_started_at', '—')}")
        else:
            st.warning("Scheduler não iniciado")

    with col2:
        st.subheader("📅 Horários de Hoje")
        st.markdown("**Fixos:**")
        for s in FIXED_SCHEDULES:
            h, m = s["time"]
            emoji_map = {"cabeça": "🧠", "pélvis": "⚡", "coração": "💖"}
            emoji = emoji_map.get(s["sanctuary"], "🌹")
            st.markdown(f"- {emoji} `{h:02d}:{m:02d}` — {s['sanctuary'].title()} ({s['theme']})")

        random_times = st.session_state.get("random_times_today", [])
        if not random_times:
            # Fallback: ler do arquivo de controle persistente
            control = load_control()
            random_times = [tuple(rt) for rt in control.get("random_times", [])]
        if random_times:
            st.markdown("**Aleatórios:**")
            for rt in random_times:
                st.markdown(f"- 🌹 `{rt[0]:02d}:{rt[1]:02d}` — Integração dos 3 Santuários")
        else:
            st.caption("Horários aleatórios serão gerados quando o scheduler iniciar um novo dia.")

    # ----------------------------------------------------------
    # Envio Manual
    # ----------------------------------------------------------
    st.divider()
    st.subheader("✉️ Envio Manual")

    msg_type = st.radio(
        "Tipo de mensagem:",
        ["Santuário da Cabeça (Intenção)", "Santuário da Pélvis (Renovação)",
         "Santuário do Coração (Reflexão)", "Integração dos 3 Santuários"],
        horizontal=True,
    )

    if st.button("🌹 Gerar e Enviar Mensagem", type="primary", use_container_width=True):
        with st.spinner("Gerando mensagem com Claude e enviando via Pushover..."):
            if "Cabeça" in msg_type:
                result = generate_and_send("fixed", "cabeça", "intenção")
            elif "Pélvis" in msg_type:
                result = generate_and_send("fixed", "pélvis", "renovação")
            elif "Coração" in msg_type:
                result = generate_and_send("fixed", "coração", "reflexão")
            else:
                result = generate_and_send("random")

            st.session_state["log"].append(result)

            if result["pushover_result"].get("success"):
                st.success("✅ Mensagem enviada com sucesso!")
            else:
                st.error(f"❌ Erro no envio: {result['pushover_result']}")

            st.markdown("**Mensagem gerada:**")
            st.info(result["message"])

    # ----------------------------------------------------------
    # Histórico Recente
    # ----------------------------------------------------------
    st.divider()
    st.subheader("📜 Mensagens Recentes")

    log = st.session_state.get("log", [])
    if log:
        for entry in reversed(log[-10:]):
            sanctuary_display = entry.get("sanctuary", "todos").title()
            with st.expander(
                f"{entry['timestamp']} — {sanctuary_display} ({entry['type']})",
                expanded=False,
            ):
                st.write(entry["message"])
                status = "✅" if entry["pushover_result"].get("success") else "❌"
                st.caption(f"Envio: {status}")
    else:
        st.caption("Nenhuma mensagem enviada ainda nesta sessão.")

    # ----------------------------------------------------------
    # Info
    # ----------------------------------------------------------
    st.divider()
    with st.expander("ℹ️ Sobre o aplicativo"):
        st.markdown("""
        **Mensageiro da Rosacruz Áurea** envia reflexões espirituais diárias
        para manter a ligação consciente com o Corpo Vivo da Escola Espiritual.

        **Horários fixos:**
        - 08:00 — Santuário da Cabeça (Intenção matinal)
        - 12:00 — Santuário da Pélvis (Renovação da vontade)
        - 20:00 — Santuário do Coração (Reflexão noturna)

        **Horários aleatórios (2x/dia):**
        - Entre 9:00-10:59 e 14:00-18:59
        - Mensagens integradoras dos 3 santuários

        As mensagens são geradas pela API do Claude com um system prompt
        rico em referências à tradição da Rosacruz Áurea e suas conexões
        com outras escolas espirituais.
        """)

    # Auto-refresh a cada 60 segundos
    time.sleep(60)
    st.rerun()


if __name__ == "__main__":
    main()