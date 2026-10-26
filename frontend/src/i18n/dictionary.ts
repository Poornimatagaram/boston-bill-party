/**
 * Static UI strings for the four supported UI languages.
 *
 * `en` is the source of truth: its shape defines the {@link Dictionary} type,
 * so a missing or misspelled key in another language is a type error. Strings
 * that depend on a value are small functions. Dynamic content from the backend
 * (summaries, finding descriptions, the appeal letter, error details) is not
 * translated here.
 */

import type { FindingType, SupportedLanguage } from "@/lib/api";

type AgentId = "extractor" | "auditor" | "rights_checker" | "skeptic" | "writer";
type AgentStrings = { name: string; description: string; activity: string };

const en = {
  header: {
    tagline: "No overcharging without representation",
    home: "Home",
    howItWorks: "How it works",
    about: "About",
    mainNav: "Main",
    openMenu: "Open menu",
    closeMenu: "Close menu",
    language: "Language",
  },
  hero: {
    badge: "AI patient advocate for Massachusetts",
    titleStart: "Stop overpaying for",
    titleHighlight: "medical bills",
    titleEnd: ".",
    subtitle:
      "Upload a bill and a team of AI agents audits every charge, checks your patient rights, and drafts an appeal letter in minutes. Results in English, Spanish, Portuguese, or Haitian Creole.",
    cta: "Analyze my bill",
    secondary: "See how it works",
    synthetic: "Synthetic data only. No real patient data.",
    imageAlt: "A patient reviewing a medical bill with a care advocate",
    chipDuplicate: "Duplicate charge",
    chipLetter: "Appeal letter ready",
  },
  howItWorks: {
    eyebrow: "How it works",
    title: "From confusing bill to appeal letter in three steps",
    steps: [
      {
        title: "Upload your bill",
        body: "Drop in a PDF of your medical bill and pick the language you want your results in.",
      },
      {
        title: "AI agents audit it",
        body: "Five agents read the bill, check the math, spot duplicates, review your patient rights, and double-check every finding.",
      },
      {
        title: "Get your appeal letter",
        body: "Review a plain-language summary and an editable appeal letter you can print, email, or download.",
      },
    ],
  },
  stats: {
    aria: "Key numbers",
    agents: "AI agents",
    languages: "Languages",
    detection: "Error detection",
  },
  uploadSection: {
    eyebrow: "Get started",
    title: "Analyze your bill",
    body: "We’ll walk you through each agent’s progress, then show your issues, a plain-language summary, and an appeal letter you can edit before sending.",
    bullets: [
      "Five specialized AI agents",
      "Checked against MA patient-rights rules",
      "Results in your language",
    ],
  },
  upload: {
    fileLabel: "Medical bill (PDF)",
    dropTitle: "Drag & drop your PDF here",
    dropHint: "or click to browse · PDF up to 10 MB",
    fileMeta: (kb: string) => `${kb} KB · click or drop to replace`,
    outputLanguage: "Output language",
    outputLanguageHint:
      "Your summary uses this language. Appeal letters are always written in English.",
    syntheticStrong: "Synthetic data only",
    syntheticRest:
      "— no real patient data. Please upload test bills, not real medical records.",
    notPdf: "That doesn’t look like a PDF. Please drop a PDF medical bill.",
    failed: "Upload failed. Please try again.",
    uploading: "Uploading…",
    analyze: "Analyze bill",
    noPdf: "No PDF handy?",
    trySample: "Try a sample bill",
    loadingSample: "Loading sample…",
    sampleFailed: "We couldn't load the sample bill. Please try again.",
    selected: (name: string) => `Selected file: ${name}`,
    demoLimit:
      "Demo limit reached. This free demo limits how many bills can be analyzed to keep costs down. Please try again later.",
  },
  about: {
    title: "About Boston Bill Party",
    body: "Boston Bill Party is a multi-agent AI patient advocate that audits medical bills and drafts appeals for Massachusetts residents. It was built at Hackachusetts 2026 to make billing errors easier to catch and easier to dispute, because no one should be overcharged without representation.",
  },
  footer: {
    syntheticStrong: "Synthetic data only.",
    syntheticBody:
      "This demo works exclusively with synthetic test bills. Never upload real patient data or protected health information.",
    builtAt: "Built at",
    github: "GitHub",
  },
  common: {
    newTab: "(opens in a new tab)",
  },
  pages: {
    progress: {
      title: "Analyzing your bill",
      subtitle: "Five specialized agents review your bill one step at a time.",
    },
    results: {
      title: "Your results",
      subtitle:
        "Here’s what our agents found, plus a ready-to-send appeal letter.",
    },
  },
  agents: {
    extractor: {
      name: "Extractor",
      description: "Reads your bill and pulls out the line items.",
      activity: "Reading your bill",
    },
    auditor: {
      name: "Auditor",
      description: "Checks the math and looks for duplicate charges.",
      activity: "Checking the math and charges",
    },
    rights_checker: {
      name: "Rights Checker",
      description: "Reviews the bill against patient-rights rules.",
      activity: "Reviewing patient rights",
    },
    skeptic: {
      name: "Skeptic",
      description: "Double-checks each finding and drops weak ones.",
      activity: "Double-checking the findings",
    },
    writer: {
      name: "Writer",
      description: "Writes your plain-language summary and appeal letter.",
      activity: "Writing your summary and appeal letter",
    },
  } as Record<AgentId, AgentStrings>,
  progress: {
    stepsCompleted: "Steps completed",
    stepsCompletedAria: (done: number, total: number) =>
      `${done} of ${total} steps completed`,
    startingUp: "Starting up",
    inProgress: "In progress",
    doneSr: "(done)",
    failed: "Failed",
    stoppedTitle: "Analysis stopped",
    problemDuring: (activity: string) =>
      `A problem occurred during “${activity}”.`,
    stalledTitle: "The analysis seems to have stalled",
    stalledBody:
      "We haven’t received a progress update in a while. The analysis may still be running — your progress so far is shown above.",
    unavailableTitle: "Analysis unavailable",
    lostLive:
      "We lost the live connection to the analysis. It may still be running.",
    lostConnection:
      "We lost connection to the analysis. Please refresh to try again.",
  },
  results: {
    loading: "Loading your results…",
    finishing: "Finishing up your analysis…",
    unavailableTitle: "Results unavailable",
    notFound: "We couldn't find this analysis session. It may have expired.",
    loadFailed: "We couldn't load the results. Please try again.",
    letterFailedTitle: "Appeal letter couldn’t be generated",
    keptWhileRetry: "Your summary is kept while you retry.",
    retry: "Retry",
    tabsAria: "Results sections",
    tabOverview: "Overview",
    tabIssues: "Issues",
    tabLetter: "Appeal Letter",
    tabLetterShort: "Letter",
    confirmedSr: "(confirmed)",
    noIssuesTitle: "No credible issues found",
    issuesFoundLabel: (n: number): string =>
      n === 1 ? "issue found" : "issues found",
    noIssuesBody: "We didn’t find credible billing errors or rights issues.",
    issuesBody: "Review the details and send your appeal letter.",
    viewIssues: "View issues",
    openLetter: "Appeal letter",
    summary: "Summary",
    noSummary: "No summary is available yet.",
    issuesEmptyTitle: "No issues detected",
    issuesEmptyBody:
      "Our agents didn’t find credible billing errors or patient-rights issues on this bill.",
  },
  issues: {
    types: {
      math_error: "Math error",
      duplicate_charge: "Duplicate charge",
      suspicious_code: "Suspicious code",
      rights_issue: "Patient-rights issue",
    } as Record<FindingType, string>,
    fallbackType: "Issue",
    confidence: (pct: number) => `${pct}% confidence`,
    confidenceAria: (pct: number) => `Confidence ${pct}%`,
    showDetails: "Show details",
    hideDetails: "Hide details",
  },
  letter: {
    title: "Appeal letter",
    hint: "Review and edit this draft. Nothing is sent until you select Confirm.",
    confirm: "Confirm appeal letter",
    confirming: "Confirming…",
    confirmed: "Confirmed",
    confirmedMsg: "Your appeal letter has been confirmed and submitted.",
    submitFailed: "We couldn't submit the letter. Please try again.",
    unavailableTitle: "Appeal letter unavailable",
    unavailableBody:
      "The letter couldn’t be generated. Use Retry above to try again.",
    notNeededTitle: "No appeal letter needed",
    notNeededBody:
      "No credible billing errors or rights issues were detected, so there is no appeal letter to review.",
  },
  translation: {
    show: "Show translation",
    hide: "Hide translation",
    heading: (language: string) => `Translation · ${language}`,
    note: "Send the English version. The translation is only to help you understand it.",
    loading: "Translating…",
    failed: "We couldn't translate the letter.",
    retry: "Try again",
    outdated: "The letter changed since this translation.",
    update: "Update translation",
    regionLabel: "Letter translation (read-only)",
  },
  actions: {
    sendTitle: "Send your letter",
    print: "Print",
    gmail: "Open in Gmail",
    outlook: "Open in Outlook",
    copy: "Copy",
    copied: "Copied",
    download: "Download .txt",
    note: "Send this to your hospital billing office or insurer.",
    copiedSr: "Letter copied to clipboard",
  },
  readAloud: {
    readAloud: "Read aloud",
    stop: "Stop",
    stopReading: "Stop reading",
    item: (label: string) => `Read aloud: ${label}`,
    labels: {
      summaryItem: "this summary item",
      issue: "this issue",
      letter: "the appeal letter",
      answer: "this answer",
      translation: "the translation",
    },
  },
  chat: {
    open: "Ask about your bill",
    close: "Close",
    closeAssistant: "Close bill assistant",
    title: "Bill assistant",
    subtitleVoice: "Type or speak a question about your bill",
    subtitleText: "Type a question about your bill",
    conversation: "Conversation",
    greeting:
      "Hi! Ask me anything about this bill, like which charges look wrong or what your rights are.",
    you: "You: ",
    assistant: "Assistant: ",
    thinking: "Thinking…",
    listening: "Listening… speak your question.",
    noSpeech:
      "We didn’t catch that — no speech was detected. Tap the mic to try again, or type your question.",
    micBlocked:
      "Microphone access was blocked. Allow microphone access, or type your question.",
    micMissing:
      "We couldn't access a microphone. Check that one is connected, or type your question.",
    micNetwork:
      "A network problem interrupted speech recognition. Try again, or type your question.",
    micGeneric: "We couldn't understand that. Try again, or type your question.",
    askByVoice: "Ask by voice",
    stopListening: "Stop listening",
    yourQuestion: "Your question",
    placeholder: "Ask a question about your bill…",
    placeholderListening: "Listening…",
    send: "Send question",
    answerFailed: "We couldn't retrieve an answer. Please try again.",
    demoLimit:
      "Demo limit reached. This free demo limits how many questions can be asked per hour. Please try again later.",
  },
};

export type Dictionary = typeof en;

const es: Dictionary = {
  header: {
    tagline: "Ningún cobro excesivo sin representación",
    home: "Inicio",
    howItWorks: "Cómo funciona",
    about: "Acerca de",
    mainNav: "Principal",
    openMenu: "Abrir menú",
    closeMenu: "Cerrar menú",
    language: "Idioma",
  },
  hero: {
    badge: "Defensor del paciente con IA para Massachusetts",
    titleStart: "Deja de pagar de más por tus",
    titleHighlight: "facturas médicas",
    titleEnd: ".",
    subtitle:
      "Sube una factura y un equipo de agentes de IA revisa cada cargo, verifica tus derechos como paciente y redacta una carta de apelación en minutos. Resultados en inglés, español, portugués o criollo haitiano.",
    cta: "Analizar mi factura",
    secondary: "Ver cómo funciona",
    synthetic: "Solo datos sintéticos. Sin datos reales de pacientes.",
    imageAlt: "Una paciente revisa una factura médica con una asesora",
    chipDuplicate: "Cargo duplicado",
    chipLetter: "Carta de apelación lista",
  },
  howItWorks: {
    eyebrow: "Cómo funciona",
    title: "De una factura confusa a una carta de apelación en tres pasos",
    steps: [
      {
        title: "Sube tu factura",
        body: "Sube el PDF de tu factura médica y elige el idioma de tus resultados.",
      },
      {
        title: "Los agentes de IA la revisan",
        body: "Cinco agentes leen la factura, revisan los cálculos, detectan duplicados, revisan tus derechos como paciente y verifican cada hallazgo.",
      },
      {
        title: "Recibe tu carta de apelación",
        body: "Revisa un resumen fácil de entender y una carta de apelación editable que puedes imprimir, enviar por correo o descargar.",
      },
    ],
  },
  stats: {
    aria: "Cifras clave",
    agents: "Agentes de IA",
    languages: "Idiomas",
    detection: "Detección de errores",
  },
  uploadSection: {
    eyebrow: "Empieza",
    title: "Analiza tu factura",
    body: "Te mostraremos el progreso de cada agente y luego tus problemas, un resumen fácil de entender y una carta de apelación que puedes editar antes de enviarla.",
    bullets: [
      "Cinco agentes de IA especializados",
      "Revisado según las normas de derechos del paciente de MA",
      "Resultados en tu idioma",
    ],
  },
  upload: {
    fileLabel: "Factura médica (PDF)",
    dropTitle: "Arrastra y suelta tu PDF aquí",
    dropHint: "o haz clic para buscar · PDF de hasta 10 MB",
    fileMeta: (kb: string) => `${kb} KB · haz clic o suelta otro para reemplazarlo`,
    outputLanguage: "Idioma de los resultados",
    outputLanguageHint:
      "Tu resumen estará en este idioma. Las cartas de apelación siempre se redactan en inglés.",
    syntheticStrong: "Solo datos sintéticos",
    syntheticRest:
      "— sin datos reales de pacientes. Sube facturas de prueba, no expedientes médicos reales.",
    notPdf: "Eso no parece un PDF. Suelta una factura médica en PDF.",
    failed: "No se pudo subir. Inténtalo de nuevo.",
    uploading: "Subiendo…",
    analyze: "Analizar factura",
    noPdf: "¿No tienes un PDF a mano?",
    trySample: "Probar una factura de ejemplo",
    loadingSample: "Cargando ejemplo…",
    sampleFailed: "No pudimos cargar la factura de ejemplo. Inténtalo de nuevo.",
    selected: (name: string) => `Archivo seleccionado: ${name}`,
    demoLimit:
      "Se alcanzó el límite de la demostración. Esta demo gratuita limita cuántas facturas se pueden analizar para controlar los costos. Inténtalo más tarde.",
  },
  about: {
    title: "Acerca de Boston Bill Party",
    body: "Boston Bill Party es un defensor del paciente con IA, formado por varios agentes, que revisa facturas médicas y redacta apelaciones para residentes de Massachusetts. Se creó en Hackachusetts 2026 para que los errores de facturación sean más fáciles de detectar y de disputar, porque nadie debería pagar de más sin representación.",
  },
  footer: {
    syntheticStrong: "Solo datos sintéticos.",
    syntheticBody:
      "Esta demostración funciona exclusivamente con facturas de prueba sintéticas. Nunca subas datos reales de pacientes ni información de salud protegida.",
    builtAt: "Creado en",
    github: "GitHub",
  },
  common: {
    newTab: "(se abre en una pestaña nueva)",
  },
  pages: {
    progress: {
      title: "Analizando tu factura",
      subtitle: "Cinco agentes especializados revisan tu factura paso a paso.",
    },
    results: {
      title: "Tus resultados",
      subtitle:
        "Esto es lo que encontraron nuestros agentes, junto con una carta de apelación lista para enviar.",
    },
  },
  agents: {
    extractor: {
      name: "Extractor",
      description: "Lee tu factura y extrae cada cargo.",
      activity: "Leyendo tu factura",
    },
    auditor: {
      name: "Auditor",
      description: "Revisa los cálculos y busca cargos duplicados.",
      activity: "Revisando los cálculos y los cargos",
    },
    rights_checker: {
      name: "Verificador de derechos",
      description: "Revisa la factura según las normas de derechos del paciente.",
      activity: "Revisando los derechos del paciente",
    },
    skeptic: {
      name: "Escéptico",
      description: "Verifica cada hallazgo y descarta los débiles.",
      activity: "Verificando los hallazgos",
    },
    writer: {
      name: "Redactor",
      description: "Redacta tu resumen y tu carta de apelación.",
      activity: "Redactando tu resumen y tu carta de apelación",
    },
  },
  progress: {
    stepsCompleted: "Pasos completados",
    stepsCompletedAria: (done: number, total: number) =>
      `${done} de ${total} pasos completados`,
    startingUp: "Iniciando",
    inProgress: "En curso",
    doneSr: "(completado)",
    failed: "Falló",
    stoppedTitle: "Análisis detenido",
    problemDuring: (activity: string) =>
      `Ocurrió un problema durante “${activity}”.`,
    stalledTitle: "El análisis parece haberse detenido",
    stalledBody:
      "No hemos recibido una actualización en un rato. Es posible que el análisis siga en curso; tu progreso hasta ahora se muestra arriba.",
    unavailableTitle: "Análisis no disponible",
    lostLive:
      "Perdimos la conexión en vivo con el análisis. Es posible que siga en curso.",
    lostConnection:
      "Perdimos la conexión con el análisis. Actualiza la página para intentarlo de nuevo.",
  },
  results: {
    loading: "Cargando tus resultados…",
    finishing: "Terminando tu análisis…",
    unavailableTitle: "Resultados no disponibles",
    notFound: "No encontramos esta sesión de análisis. Puede que haya vencido.",
    loadFailed: "No pudimos cargar los resultados. Inténtalo de nuevo.",
    letterFailedTitle: "No se pudo generar la carta de apelación",
    keptWhileRetry: "Tu resumen se conserva mientras lo vuelves a intentar.",
    retry: "Reintentar",
    tabsAria: "Secciones de resultados",
    tabOverview: "Vista general",
    tabIssues: "Problemas",
    tabLetter: "Carta de apelación",
    tabLetterShort: "Carta",
    confirmedSr: "(confirmada)",
    noIssuesTitle: "No se encontraron problemas creíbles",
    issuesFoundLabel: (n: number) =>
      n === 1 ? "problema encontrado" : "problemas encontrados",
    noIssuesBody:
      "No encontramos errores de facturación ni problemas de derechos creíbles.",
    issuesBody: "Revisa los detalles y envía tu carta de apelación.",
    viewIssues: "Ver problemas",
    openLetter: "Carta de apelación",
    summary: "Resumen",
    noSummary: "Todavía no hay un resumen disponible.",
    issuesEmptyTitle: "No se detectaron problemas",
    issuesEmptyBody:
      "Nuestros agentes no encontraron errores de facturación ni problemas de derechos del paciente creíbles en esta factura.",
  },
  issues: {
    types: {
      math_error: "Error de cálculo",
      duplicate_charge: "Cargo duplicado",
      suspicious_code: "Código sospechoso",
      rights_issue: "Problema de derechos del paciente",
    },
    fallbackType: "Problema",
    confidence: (pct: number) => `${pct}% de confianza`,
    confidenceAria: (pct: number) => `Confianza: ${pct}%`,
    showDetails: "Ver detalles",
    hideDetails: "Ocultar detalles",
  },
  letter: {
    title: "Carta de apelación",
    hint: "Revisa y edita este borrador. No se envía nada hasta que selecciones Confirmar.",
    confirm: "Confirmar carta de apelación",
    confirming: "Confirmando…",
    confirmed: "Confirmada",
    confirmedMsg: "Tu carta de apelación se confirmó y se envió.",
    submitFailed: "No pudimos enviar la carta. Inténtalo de nuevo.",
    unavailableTitle: "Carta de apelación no disponible",
    unavailableBody:
      "No se pudo generar la carta. Usa Reintentar arriba para intentarlo de nuevo.",
    notNeededTitle: "No se necesita carta de apelación",
    notNeededBody:
      "No se detectaron errores de facturación ni problemas de derechos creíbles, así que no hay carta de apelación que revisar.",
  },
  translation: {
    show: "Ver traducción",
    hide: "Ocultar traducción",
    heading: (language: string) => `Traducción · ${language}`,
    note: "Envía la versión en inglés. La traducción es solo para ayudarte a entenderla.",
    loading: "Traduciendo…",
    failed: "No pudimos traducir la carta.",
    retry: "Intentar de nuevo",
    outdated: "La carta cambió desde esta traducción.",
    update: "Actualizar traducción",
    regionLabel: "Traducción de la carta (solo lectura)",
  },
  actions: {
    sendTitle: "Envía tu carta",
    print: "Imprimir",
    gmail: "Abrir en Gmail",
    outlook: "Abrir en Outlook",
    copy: "Copiar",
    copied: "Copiada",
    download: "Descargar .txt",
    note: "Envíala a la oficina de facturación de tu hospital o a tu aseguradora.",
    copiedSr: "Carta copiada al portapapeles",
  },
  readAloud: {
    readAloud: "Leer en voz alta",
    stop: "Detener",
    stopReading: "Dejar de leer",
    item: (label: string) => `Leer en voz alta: ${label}`,
    labels: {
      summaryItem: "este punto del resumen",
      issue: "este problema",
      letter: "la carta de apelación",
      answer: "esta respuesta",
      translation: "la traducción",
    },
  },
  chat: {
    open: "Pregunta sobre tu factura",
    close: "Cerrar",
    closeAssistant: "Cerrar el asistente",
    title: "Asistente de facturas",
    subtitleVoice: "Escribe o di una pregunta sobre tu factura",
    subtitleText: "Escribe una pregunta sobre tu factura",
    conversation: "Conversación",
    greeting:
      "¡Hola! Pregúntame lo que quieras sobre esta factura, como qué cargos parecen incorrectos o cuáles son tus derechos.",
    you: "Tú: ",
    assistant: "Asistente: ",
    thinking: "Pensando…",
    listening: "Escuchando… di tu pregunta.",
    noSpeech:
      "No te escuchamos: no se detectó voz. Toca el micrófono para intentarlo de nuevo o escribe tu pregunta.",
    micBlocked:
      "Se bloqueó el acceso al micrófono. Permite el acceso o escribe tu pregunta.",
    micMissing:
      "No pudimos acceder a un micrófono. Verifica que esté conectado o escribe tu pregunta.",
    micNetwork:
      "Un problema de red interrumpió el reconocimiento de voz. Inténtalo de nuevo o escribe tu pregunta.",
    micGeneric: "No pudimos entenderte. Inténtalo de nuevo o escribe tu pregunta.",
    askByVoice: "Preguntar por voz",
    stopListening: "Dejar de escuchar",
    yourQuestion: "Tu pregunta",
    placeholder: "Haz una pregunta sobre tu factura…",
    placeholderListening: "Escuchando…",
    send: "Enviar pregunta",
    answerFailed: "No pudimos obtener una respuesta. Inténtalo de nuevo.",
    demoLimit:
      "Se alcanzó el límite de la demostración. Esta demo gratuita limita cuántas preguntas se pueden hacer por hora. Inténtalo más tarde.",
  },
};

const pt: Dictionary = {
  header: {
    tagline: "Nenhuma cobrança abusiva sem representação",
    home: "Início",
    howItWorks: "Como funciona",
    about: "Sobre",
    mainNav: "Principal",
    openMenu: "Abrir menu",
    closeMenu: "Fechar menu",
    language: "Idioma",
  },
  hero: {
    badge: "Defensor do paciente com IA para Massachusetts",
    titleStart: "Pare de pagar a mais pelas suas",
    titleHighlight: "contas médicas",
    titleEnd: ".",
    subtitle:
      "Envie uma conta e uma equipe de agentes de IA analisa cada cobrança, verifica seus direitos como paciente e redige uma carta de contestação em minutos. Resultados em inglês, espanhol, português ou crioulo haitiano.",
    cta: "Analisar minha conta",
    secondary: "Veja como funciona",
    synthetic: "Apenas dados sintéticos. Nenhum dado real de paciente.",
    imageAlt: "Uma paciente analisando uma conta médica com uma assistente",
    chipDuplicate: "Cobrança duplicada",
    chipLetter: "Carta de contestação pronta",
  },
  howItWorks: {
    eyebrow: "Como funciona",
    title: "De uma conta confusa a uma carta de contestação em três passos",
    steps: [
      {
        title: "Envie sua conta",
        body: "Envie o PDF da sua conta médica e escolha o idioma dos seus resultados.",
      },
      {
        title: "Agentes de IA fazem a auditoria",
        body: "Cinco agentes leem a conta, conferem os cálculos, encontram duplicidades, analisam seus direitos como paciente e verificam cada constatação.",
      },
      {
        title: "Receba sua carta de contestação",
        body: "Confira um resumo em linguagem simples e uma carta de contestação editável que você pode imprimir, enviar por e-mail ou baixar.",
      },
    ],
  },
  stats: {
    aria: "Números principais",
    agents: "Agentes de IA",
    languages: "Idiomas",
    detection: "Detecção de erros",
  },
  uploadSection: {
    eyebrow: "Comece agora",
    title: "Analise sua conta",
    body: "Mostraremos o progresso de cada agente e, depois, os problemas encontrados, um resumo em linguagem simples e uma carta de contestação que você pode editar antes de enviar.",
    bullets: [
      "Cinco agentes de IA especializados",
      "Verificado com base nas regras de direitos do paciente de MA",
      "Resultados no seu idioma",
    ],
  },
  upload: {
    fileLabel: "Conta médica (PDF)",
    dropTitle: "Arraste e solte seu PDF aqui",
    dropHint: "ou clique para procurar · PDF de até 10 MB",
    fileMeta: (kb: string) => `${kb} KB · clique ou solte outro para substituir`,
    outputLanguage: "Idioma dos resultados",
    outputLanguageHint:
      "Seu resumo ficará neste idioma. As cartas de contestação são sempre escritas em inglês.",
    syntheticStrong: "Apenas dados sintéticos",
    syntheticRest:
      "— nenhum dado real de paciente. Envie contas de teste, não prontuários médicos reais.",
    notPdf: "Isso não parece um PDF. Solte uma conta médica em PDF.",
    failed: "Falha no envio. Tente novamente.",
    uploading: "Enviando…",
    analyze: "Analisar conta",
    noPdf: "Não tem um PDF à mão?",
    trySample: "Experimentar uma conta de exemplo",
    loadingSample: "Carregando exemplo…",
    sampleFailed: "Não foi possível carregar a conta de exemplo. Tente novamente.",
    selected: (name: string) => `Arquivo selecionado: ${name}`,
    demoLimit:
      "Limite da demonstração atingido. Esta demo gratuita limita quantas contas podem ser analisadas para controlar os custos. Tente novamente mais tarde.",
  },
  about: {
    title: "Sobre o Boston Bill Party",
    body: "O Boston Bill Party é um defensor do paciente com IA, formado por vários agentes, que audita contas médicas e redige contestações para moradores de Massachusetts. Ele foi criado no Hackachusetts 2026 para tornar os erros de cobrança mais fáceis de identificar e de contestar, porque ninguém deve pagar a mais sem representação.",
  },
  footer: {
    syntheticStrong: "Apenas dados sintéticos.",
    syntheticBody:
      "Esta demonstração funciona exclusivamente com contas de teste sintéticas. Nunca envie dados reais de pacientes nem informações de saúde protegidas.",
    builtAt: "Criado no",
    github: "GitHub",
  },
  common: {
    newTab: "(abre em uma nova aba)",
  },
  pages: {
    progress: {
      title: "Analisando sua conta",
      subtitle:
        "Cinco agentes especializados analisam sua conta, uma etapa de cada vez.",
    },
    results: {
      title: "Seus resultados",
      subtitle:
        "Veja o que nossos agentes encontraram, além de uma carta de contestação pronta para enviar.",
    },
  },
  agents: {
    extractor: {
      name: "Extrator",
      description: "Lê sua conta e extrai cada item cobrado.",
      activity: "Lendo sua conta",
    },
    auditor: {
      name: "Auditor",
      description: "Confere os cálculos e procura cobranças duplicadas.",
      activity: "Conferindo os cálculos e as cobranças",
    },
    rights_checker: {
      name: "Verificador de direitos",
      description: "Analisa a conta com base nas regras de direitos do paciente.",
      activity: "Analisando os direitos do paciente",
    },
    skeptic: {
      name: "Cético",
      description: "Verifica cada constatação e descarta as fracas.",
      activity: "Verificando as constatações",
    },
    writer: {
      name: "Redator",
      description: "Redige seu resumo e sua carta de contestação.",
      activity: "Redigindo seu resumo e sua carta de contestação",
    },
  },
  progress: {
    stepsCompleted: "Etapas concluídas",
    stepsCompletedAria: (done: number, total: number) =>
      `${done} de ${total} etapas concluídas`,
    startingUp: "Iniciando",
    inProgress: "Em andamento",
    doneSr: "(concluída)",
    failed: "Falhou",
    stoppedTitle: "Análise interrompida",
    problemDuring: (activity: string) =>
      `Ocorreu um problema durante “${activity}”.`,
    stalledTitle: "A análise parece ter travado",
    stalledBody:
      "Não recebemos uma atualização há algum tempo. A análise ainda pode estar em andamento — seu progresso até agora aparece acima.",
    unavailableTitle: "Análise indisponível",
    lostLive:
      "Perdemos a conexão em tempo real com a análise. Ela ainda pode estar em andamento.",
    lostConnection:
      "Perdemos a conexão com a análise. Atualize a página para tentar novamente.",
  },
  results: {
    loading: "Carregando seus resultados…",
    finishing: "Finalizando sua análise…",
    unavailableTitle: "Resultados indisponíveis",
    notFound: "Não encontramos esta sessão de análise. Ela pode ter expirado.",
    loadFailed: "Não foi possível carregar os resultados. Tente novamente.",
    letterFailedTitle: "Não foi possível gerar a carta de contestação",
    keptWhileRetry: "Seu resumo é mantido enquanto você tenta novamente.",
    retry: "Tentar novamente",
    tabsAria: "Seções dos resultados",
    tabOverview: "Visão geral",
    tabIssues: "Problemas",
    tabLetter: "Carta de contestação",
    tabLetterShort: "Carta",
    confirmedSr: "(confirmada)",
    noIssuesTitle: "Nenhum problema relevante encontrado",
    issuesFoundLabel: (n: number) =>
      n === 1 ? "problema encontrado" : "problemas encontrados",
    noIssuesBody:
      "Não encontramos erros de cobrança nem problemas de direitos relevantes.",
    issuesBody: "Confira os detalhes e envie sua carta de contestação.",
    viewIssues: "Ver problemas",
    openLetter: "Carta de contestação",
    summary: "Resumo",
    noSummary: "Ainda não há um resumo disponível.",
    issuesEmptyTitle: "Nenhum problema detectado",
    issuesEmptyBody:
      "Nossos agentes não encontraram erros de cobrança nem problemas de direitos do paciente relevantes nesta conta.",
  },
  issues: {
    types: {
      math_error: "Erro de cálculo",
      duplicate_charge: "Cobrança duplicada",
      suspicious_code: "Código suspeito",
      rights_issue: "Problema de direitos do paciente",
    },
    fallbackType: "Problema",
    confidence: (pct: number) => `${pct}% de confiança`,
    confidenceAria: (pct: number) => `Confiança: ${pct}%`,
    showDetails: "Mostrar detalhes",
    hideDetails: "Ocultar detalhes",
  },
  letter: {
    title: "Carta de contestação",
    hint: "Revise e edite este rascunho. Nada é enviado até você selecionar Confirmar.",
    confirm: "Confirmar carta de contestação",
    confirming: "Confirmando…",
    confirmed: "Confirmada",
    confirmedMsg: "Sua carta de contestação foi confirmada e enviada.",
    submitFailed: "Não foi possível enviar a carta. Tente novamente.",
    unavailableTitle: "Carta de contestação indisponível",
    unavailableBody:
      "Não foi possível gerar a carta. Use Tentar novamente acima.",
    notNeededTitle: "Nenhuma carta de contestação necessária",
    notNeededBody:
      "Não foram detectados erros de cobrança nem problemas de direitos relevantes, então não há carta de contestação para revisar.",
  },
  translation: {
    show: "Ver tradução",
    hide: "Ocultar tradução",
    heading: (language: string) => `Tradução · ${language}`,
    note: "Envie a versão em inglês. A tradução serve apenas para ajudar você a entendê-la.",
    loading: "Traduzindo…",
    failed: "Não foi possível traduzir a carta.",
    retry: "Tentar novamente",
    outdated: "A carta mudou desde esta tradução.",
    update: "Atualizar tradução",
    regionLabel: "Tradução da carta (somente leitura)",
  },
  actions: {
    sendTitle: "Envie sua carta",
    print: "Imprimir",
    gmail: "Abrir no Gmail",
    outlook: "Abrir no Outlook",
    copy: "Copiar",
    copied: "Copiada",
    download: "Baixar .txt",
    note: "Envie-a para o setor de cobrança do seu hospital ou para o seu plano de saúde.",
    copiedSr: "Carta copiada para a área de transferência",
  },
  readAloud: {
    readAloud: "Ler em voz alta",
    stop: "Parar",
    stopReading: "Parar leitura",
    item: (label: string) => `Ler em voz alta: ${label}`,
    labels: {
      summaryItem: "este item do resumo",
      issue: "este problema",
      letter: "a carta de contestação",
      answer: "esta resposta",
      translation: "a tradução",
    },
  },
  chat: {
    open: "Pergunte sobre sua conta",
    close: "Fechar",
    closeAssistant: "Fechar o assistente",
    title: "Assistente da conta",
    subtitleVoice: "Digite ou fale uma pergunta sobre sua conta",
    subtitleText: "Digite uma pergunta sobre sua conta",
    conversation: "Conversa",
    greeting:
      "Olá! Pergunte o que quiser sobre esta conta, como quais cobranças parecem erradas ou quais são os seus direitos.",
    you: "Você: ",
    assistant: "Assistente: ",
    thinking: "Pensando…",
    listening: "Ouvindo… fale sua pergunta.",
    noSpeech:
      "Não entendemos — nenhuma fala foi detectada. Toque no microfone para tentar novamente ou digite sua pergunta.",
    micBlocked:
      "O acesso ao microfone foi bloqueado. Permita o acesso ou digite sua pergunta.",
    micMissing:
      "Não foi possível acessar um microfone. Verifique se há um conectado ou digite sua pergunta.",
    micNetwork:
      "Um problema de rede interrompeu o reconhecimento de voz. Tente novamente ou digite sua pergunta.",
    micGeneric: "Não conseguimos entender. Tente novamente ou digite sua pergunta.",
    askByVoice: "Perguntar por voz",
    stopListening: "Parar de ouvir",
    yourQuestion: "Sua pergunta",
    placeholder: "Faça uma pergunta sobre sua conta…",
    placeholderListening: "Ouvindo…",
    send: "Enviar pergunta",
    answerFailed: "Não foi possível obter uma resposta. Tente novamente.",
    demoLimit:
      "Limite da demonstração atingido. Esta demo gratuita limita quantas perguntas podem ser feitas por hora. Tente novamente mais tarde.",
  },
};

const ht: Dictionary = {
  header: {
    tagline: "Pa gen twòp frè san reprezantasyon",
    home: "Akèy",
    howItWorks: "Kijan sa mache",
    about: "Konsènan nou",
    mainNav: "Prensipal",
    openMenu: "Louvri meni an",
    closeMenu: "Fèmen meni an",
    language: "Lang",
  },
  hero: {
    badge: "Defansè pasyan ak IA pou Massachusetts",
    titleStart: "Sispann peye twòp pou",
    titleHighlight: "bòdwo medikal ou yo",
    titleEnd: ".",
    subtitle:
      "Voye yon bòdwo epi yon ekip ajan IA ap verifye chak frè, tcheke dwa ou kòm pasyan, epi ekri yon lèt apèl nan kèk minit. Rezilta yo disponib an anglè, panyòl, pòtigè oswa kreyòl ayisyen.",
    cta: "Analize bòdwo mwen",
    secondary: "Gade kijan sa mache",
    synthetic: "Done sentetik sèlman. Pa gen vrè done pasyan.",
    imageAlt: "Yon pasyan k ap egzamine yon bòdwo medikal ak yon konseyè",
    chipDuplicate: "Frè an doub",
    chipLetter: "Lèt apèl la pare",
  },
  howItWorks: {
    eyebrow: "Kijan sa mache",
    title: "Soti nan yon bòdwo ki konfizyon rive nan yon lèt apèl an twa etap",
    steps: [
      {
        title: "Voye bòdwo ou",
        body: "Mete PDF bòdwo medikal ou a epi chwazi lang ou vle pou rezilta yo.",
      },
      {
        title: "Ajan IA yo verifye l",
        body: "Senk ajan li bòdwo a, tcheke kalkil yo, jwenn frè ki an doub, revize dwa ou kòm pasyan, epi verifye chak rezilta.",
      },
      {
        title: "Resevwa lèt apèl ou",
        body: "Li yon rezime ki fasil pou konprann ak yon lèt apèl ou ka modifye, enprime, voye pa imèl oswa telechaje.",
      },
    ],
  },
  stats: {
    aria: "Chif enpòtan",
    agents: "Ajan IA",
    languages: "Lang",
    detection: "Deteksyon erè",
  },
  uploadSection: {
    eyebrow: "Kòmanse",
    title: "Analize bòdwo ou",
    body: "N ap montre w pwogrè chak ajan, apre sa n ap montre w pwoblèm yo, yon rezime ki fasil pou konprann, ak yon lèt apèl ou ka modifye anvan ou voye l.",
    bullets: [
      "Senk ajan IA espesyalize",
      "Verifye dapre règ dwa pasyan Massachusetts",
      "Rezilta nan lang ou",
    ],
  },
  upload: {
    fileLabel: "Bòdwo medikal (PDF)",
    dropTitle: "Trennen epi lage PDF ou a la a",
    dropHint: "oswa klike pou chèche · PDF jiska 10 MB",
    fileMeta: (kb: string) => `${kb} KB · klike oswa lage yon lòt pou ranplase l`,
    outputLanguage: "Lang rezilta yo",
    outputLanguageHint:
      "Rezime ou a ap nan lang sa a. Lèt apèl yo toujou ekri an anglè.",
    syntheticStrong: "Done sentetik sèlman",
    syntheticRest:
      "— pa gen vrè done pasyan. Tanpri voye bòdwo tès, pa vrè dosye medikal.",
    notPdf: "Sa pa sanble yon PDF. Tanpri lage yon bòdwo medikal an PDF.",
    failed: "Nou pa t ka voye l. Tanpri eseye ankò.",
    uploading: "N ap voye l…",
    analyze: "Analize bòdwo a",
    noPdf: "Ou pa gen yon PDF sou ou?",
    trySample: "Eseye yon bòdwo egzanp",
    loadingSample: "N ap chaje egzanp lan…",
    sampleFailed: "Nou pa t ka chaje bòdwo egzanp lan. Tanpri eseye ankò.",
    selected: (name: string) => `Fichye ou chwazi a: ${name}`,
    demoLimit:
      "Ou rive nan limit demo a. Demo gratis sa a limite konbyen bòdwo yo ka analize pou kenbe depans yo ba. Tanpri eseye ankò pita.",
  },
  about: {
    title: "Konsènan Boston Bill Party",
    body: "Boston Bill Party se yon defansè pasyan ak IA, ki fèt ak plizyè ajan, ki verifye bòdwo medikal epi ekri apèl pou moun ki rete Massachusetts. Nou te kreye l nan Hackachusetts 2026 pou fè erè nan bòdwo yo pi fasil pou jwenn e pi fasil pou konteste, paske pèsonn pa ta dwe peye twòp san reprezantasyon.",
  },
  footer: {
    syntheticStrong: "Done sentetik sèlman.",
    syntheticBody:
      "Demonstrasyon sa a mache sèlman ak bòdwo tès sentetik. Pa janm voye vrè done pasyan oswa enfòmasyon sante ki pwoteje.",
    builtAt: "Kreye nan",
    github: "GitHub",
  },
  common: {
    newTab: "(ap louvri nan yon nouvo onglè)",
  },
  pages: {
    progress: {
      title: "N ap analize bòdwo ou",
      subtitle: "Senk ajan espesyalize ap revize bòdwo ou, etap pa etap.",
    },
    results: {
      title: "Rezilta ou yo",
      subtitle:
        "Men sa ajan nou yo jwenn, ansanm ak yon lèt apèl ki pare pou voye.",
    },
  },
  agents: {
    extractor: {
      name: "Ekstraktè",
      description: "Li bòdwo ou epi rale chak frè.",
      activity: "N ap li bòdwo ou",
    },
    auditor: {
      name: "Oditè",
      description: "Tcheke kalkil yo epi chèche frè ki an doub.",
      activity: "N ap tcheke kalkil yo ak frè yo",
    },
    rights_checker: {
      name: "Verifikatè dwa",
      description: "Revize bòdwo a dapre règ dwa pasyan yo.",
      activity: "N ap revize dwa pasyan yo",
    },
    skeptic: {
      name: "Kritik",
      description: "Verifye chak rezilta epi retire sa ki fèb yo.",
      activity: "N ap verifye rezilta yo",
    },
    writer: {
      name: "Redaktè",
      description: "Ekri rezime ou ak lèt apèl ou.",
      activity: "N ap ekri rezime ou ak lèt apèl ou",
    },
  },
  progress: {
    stepsCompleted: "Etap ki fini",
    stepsCompletedAria: (done: number, total: number) =>
      `${done} sou ${total} etap fini`,
    startingUp: "N ap kòmanse",
    inProgress: "Ap fèt",
    doneSr: "(fini)",
    failed: "Echwe",
    stoppedTitle: "Analiz la kanpe",
    problemDuring: (activity: string) =>
      `Yon pwoblèm rive pandan “${activity}”.`,
    stalledTitle: "Analiz la sanble li bloke",
    stalledBody:
      "Nou poko resevwa nouvèl sou pwogrè a depi yon ti tan. Analiz la ka toujou ap fèt — pwogrè ou jiskaprezan parèt anlè a.",
    unavailableTitle: "Analiz la pa disponib",
    lostLive: "Nou pèdi koneksyon an dirèk ak analiz la. Li ka toujou ap fèt.",
    lostConnection:
      "Nou pèdi koneksyon ak analiz la. Tanpri rafrechi paj la pou eseye ankò.",
  },
  results: {
    loading: "N ap chaje rezilta ou yo…",
    finishing: "N ap fini analiz ou a…",
    unavailableTitle: "Rezilta yo pa disponib",
    notFound: "Nou pa jwenn sesyon analiz sa a. Li ka ekspire.",
    loadFailed: "Nou pa t ka chaje rezilta yo. Tanpri eseye ankò.",
    letterFailedTitle: "Nou pa t ka kreye lèt apèl la",
    keptWhileRetry: "Nou kenbe rezime ou pandan w ap eseye ankò.",
    retry: "Eseye ankò",
    tabsAria: "Seksyon rezilta yo",
    tabOverview: "Apèsi",
    tabIssues: "Pwoblèm",
    tabLetter: "Lèt apèl",
    tabLetterShort: "Lèt",
    confirmedSr: "(konfime)",
    noIssuesTitle: "Nou pa jwenn okenn pwoblèm serye",
    issuesFoundLabel: () => "pwoblèm nou jwenn",
    noIssuesBody:
      "Nou pa jwenn okenn erè nan bòdwo a oswa pwoblèm dwa ki serye.",
    issuesBody: "Gade detay yo epi voye lèt apèl ou.",
    viewIssues: "Gade pwoblèm yo",
    openLetter: "Lèt apèl",
    summary: "Rezime",
    noSummary: "Poko gen rezime.",
    issuesEmptyTitle: "Nou pa detekte okenn pwoblèm",
    issuesEmptyBody:
      "Ajan nou yo pa jwenn okenn erè nan bòdwo a oswa pwoblèm dwa pasyan ki serye.",
  },
  issues: {
    types: {
      math_error: "Erè kalkil",
      duplicate_charge: "Frè an doub",
      suspicious_code: "Kòd sispèk",
      rights_issue: "Pwoblèm dwa pasyan",
    },
    fallbackType: "Pwoblèm",
    confidence: (pct: number) => `${pct}% konfyans`,
    confidenceAria: (pct: number) => `Konfyans: ${pct}%`,
    showDetails: "Montre detay yo",
    hideDetails: "Kache detay yo",
  },
  letter: {
    title: "Lèt apèl",
    hint: "Revize epi modifye bouyon sa a. Anyen p ap voye toutotan ou pa chwazi Konfime.",
    confirm: "Konfime lèt apèl la",
    confirming: "N ap konfime…",
    confirmed: "Konfime",
    confirmedMsg: "Lèt apèl ou a konfime epi li voye.",
    submitFailed: "Nou pa t ka voye lèt la. Tanpri eseye ankò.",
    unavailableTitle: "Lèt apèl la pa disponib",
    unavailableBody: "Nou pa t ka kreye lèt la. Sèvi ak Eseye ankò anlè a.",
    notNeededTitle: "Pa bezwen lèt apèl",
    notNeededBody:
      "Nou pa detekte okenn erè nan bòdwo a oswa pwoblèm dwa ki serye, kidonk pa gen lèt apèl pou revize.",
  },
  translation: {
    show: "Montre tradiksyon an",
    hide: "Kache tradiksyon an",
    heading: (language: string) => `Tradiksyon · ${language}`,
    note: "Voye vèsyon an anglè a. Tradiksyon an la sèlman pou ede w konprann li.",
    loading: "N ap tradui…",
    failed: "Nou pa t ka tradui lèt la.",
    retry: "Eseye ankò",
    outdated: "Lèt la chanje depi tradiksyon sa a.",
    update: "Mete tradiksyon an ajou",
    regionLabel: "Tradiksyon lèt la (pou li sèlman)",
  },
  actions: {
    sendTitle: "Voye lèt ou",
    print: "Enprime",
    gmail: "Louvri nan Gmail",
    outlook: "Louvri nan Outlook",
    copy: "Kopye",
    copied: "Kopye",
    download: "Telechaje .txt",
    note: "Voye l bay biwo faktirasyon lopital ou a oswa asirans ou.",
    copiedSr: "Lèt la kopye",
  },
  readAloud: {
    readAloud: "Li awotvwa",
    stop: "Kanpe",
    stopReading: "Sispann li",
    item: (label: string) => `Li awotvwa: ${label}`,
    labels: {
      summaryItem: "pwen rezime sa a",
      issue: "pwoblèm sa a",
      letter: "lèt apèl la",
      answer: "repons sa a",
      translation: "tradiksyon an",
    },
  },
  chat: {
    open: "Poze kesyon sou bòdwo ou",
    close: "Fèmen",
    closeAssistant: "Fèmen asistan an",
    title: "Asistan bòdwo",
    subtitleVoice: "Ekri oswa di yon kesyon sou bòdwo ou",
    subtitleText: "Ekri yon kesyon sou bòdwo ou",
    conversation: "Konvèsasyon",
    greeting:
      "Bonjou! Poze m nenpòt kesyon sou bòdwo sa a, tankou ki frè ki sanble pa kòrèk oswa ki dwa ou genyen.",
    you: "Ou menm: ",
    assistant: "Asistan: ",
    thinking: "M ap reflechi…",
    listening: "M ap koute… di kesyon ou.",
    noSpeech:
      "Nou pa tande anyen — pa gen vwa ki detekte. Peze mikwo a pou eseye ankò, oswa ekri kesyon ou.",
    micBlocked: "Aksè mikwo a bloke. Pèmèt aksè a, oswa ekri kesyon ou.",
    micMissing:
      "Nou pa t ka jwenn yon mikwo. Tcheke si gen youn ki konekte, oswa ekri kesyon ou.",
    micNetwork:
      "Yon pwoblèm rezo entewonp rekonesans vwa a. Eseye ankò, oswa ekri kesyon ou.",
    micGeneric: "Nou pa t konprann. Eseye ankò, oswa ekri kesyon ou.",
    askByVoice: "Poze kesyon ak vwa",
    stopListening: "Sispann koute",
    yourQuestion: "Kesyon ou",
    placeholder: "Poze yon kesyon sou bòdwo ou…",
    placeholderListening: "M ap koute…",
    send: "Voye kesyon an",
    answerFailed: "Nou pa t ka jwenn yon repons. Tanpri eseye ankò.",
    demoLimit:
      "Ou rive nan limit demo a. Demo gratis sa a limite konbyen kesyon yo ka poze chak èdtan. Tanpri eseye ankò pita.",
  },
};

export const dictionaries: Record<SupportedLanguage, Dictionary> = { en, es, pt, ht };
