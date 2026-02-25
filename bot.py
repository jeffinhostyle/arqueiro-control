import os
import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import re
import random
import string
import io
import speech_recognition as sr
from pydub import AudioSegment
import tempfile

# ==================== COLOQUE SEUS DADOS AQUI ====================
TOKEN = "8778081445:AAF8PEnPHntpnN3wjqNGAfTzWNPhJV_4VxM"  # COLE SEU TOKEN AQUI (do BotFather)
ADMIN_ID = 5052937721  # COLE SEU ID AQUI (do @userinfobot)
CONTATO = "@jeffinhooliveira"  # COLE SEU @ DO TELEGRAM PARA CONTATO
# ================================================================

# ==================== BANCO DE DADOS ====================
def init_db():
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    
    # Usuários autorizados
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios
                 (id INTEGER PRIMARY KEY,
                  telegram_id INTEGER UNIQUE,
                  nome TEXT,
                  tipo TEXT DEFAULT 'cliente',
                  plano TEXT,
                  data_expiracao TEXT,
                  ativo INTEGER DEFAULT 0)''')
    
    # Códigos de acesso
    c.execute('''CREATE TABLE IF NOT EXISTS codigos
                 (id INTEGER PRIMARY KEY,
                  codigo TEXT UNIQUE,
                  dias INTEGER,
                  criado_por INTEGER,
                  usado_por INTEGER,
                  data_criacao TEXT,
                  data_uso TEXT,
                  ativo INTEGER DEFAULT 1)''')
    
    # Produtos/Serviços
    c.execute('''CREATE TABLE IF NOT EXISTS produtos
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  nome TEXT,
                  preco REAL,
                  categoria TEXT,
                  ativo INTEGER DEFAULT 1)''')
    
    # Vendas/Transações
    c.execute('''CREATE TABLE IF NOT EXISTS vendas
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  produto_id INTEGER,
                  produto_nome TEXT,
                  cliente_nome TEXT,
                  valor REAL,
                  quantidade INTEGER DEFAULT 1,
                  data TEXT,
                  observacao TEXT)''')
    
    # Transações financeiras (gastos/ganhos)
    c.execute('''CREATE TABLE IF NOT EXISTS transacoes
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  tipo TEXT,
                  descricao TEXT,
                  valor REAL,
                  data TEXT,
                  categoria TEXT DEFAULT 'geral')''')
    
    conn.commit()
    conn.close()

# ==================== FUNÇÕES AUXILIARES ====================

def gerar_codigo(tamanho=8):
    """Gera código único"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=tamanho))

def verificar_acesso(user_id):
    """Verifica se usuário tem acesso"""
    if user_id == ADMIN_ID:  # Admin tem acesso vitalício
        return True
    
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    c.execute("SELECT ativo, data_expiracao FROM usuarios WHERE telegram_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result and result[0] == 1:
        if result[1]:  # Tem data de expiração
            try:
                expiracao = datetime.strptime(result[1], "%Y-%m-%d")
                if expiracao > datetime.now():
                    return True
            except:
                return True  # Vitalício (sem data)
        else:
            return True  # Vitalício
    return False

# ==================== SISTEMA DE CÓDIGOS ====================

async def codigos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gerar códigos de acesso (só admin)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso restrito!")
        return
    
    keyboard = [
        [InlineKeyboardButton("🎫 Gerar Código 7 dias", callback_data="codigo_7")],
        [InlineKeyboardButton("🎫 Gerar Código 15 dias", callback_data="codigo_15")],
        [InlineKeyboardButton("🎫 Gerar Código 30 dias", callback_data="codigo_30")],
        [InlineKeyboardButton("🎫 Gerar Código Vitalício", callback_data="codigo_vitalicio")],
        [InlineKeyboardButton("📋 Listar Códigos", callback_data="listar_codigos")],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎫 *GERENCIAR CÓDIGOS*\n\n"
        "Escolha uma opção:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def processar_codigos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa criação de códigos"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    dias_map = {
        'codigo_7': 7,
        'codigo_15': 15,
        'codigo_30': 30,
        'codigo_vitalicio': None  # None = vitalício
    }
    
    if query.data in dias_map:
        dias = dias_map[query.data]
        codigo = gerar_codigo()
        
        conn = sqlite3.connect('sistema.db')
        c = conn.cursor()
        c.execute('''INSERT INTO codigos (codigo, dias, criado_por, data_criacao)
                     VALUES (?, ?, ?, ?)''',
                  (codigo, dias, ADMIN_ID, datetime.now()))
        conn.commit()
        conn.close()
        
        tipo = "VITALÍCIO" if dias is None else f"{dias} DIAS"
        
        await query.edit_message_text(
            f"✅ *Código Gerado com Sucesso!*\n\n"
            f"📌 *Código:* `{codigo}`\n"
            f"⏳ *Tipo:* {tipo}\n\n"
            f"Para usar: /usar {codigo}",
            parse_mode='Markdown'
        )
    
    elif query.data == "listar_codigos":
        conn = sqlite3.connect('sistema.db')
        c = conn.cursor()
        c.execute('''SELECT codigo, dias, data_criacao, usado_por, ativo 
                     FROM codigos ORDER BY data_criacao DESC LIMIT 10''')
        codigos = c.fetchall()
        conn.close()
        
        if not codigos:
            await query.edit_message_text("📋 Nenhum código encontrado.")
            return
        
        texto = "📋 *ÚLTIMOS CÓDIGOS*\n\n"
        for cod, dias, criacao, usado, ativo in codigos:
            status = "✅ Ativo" if ativo else "❌ Usado"
            tipo = "Vitalício" if dias is None else f"{dias} dias"
            texto += f"`{cod}` - {tipo}\n"
            texto += f"📅 {criacao[:10]} - {status}\n"
            texto += "─" * 20 + "\n"
        
        await query.edit_message_text(texto, parse_mode='Markdown')

async def usar_codigo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usuário usa código para ativar acesso"""
    if not context.args:
        await update.message.reply_text("Use: /usar [CÓDIGO]")
        return
    
    codigo = context.args[0].upper()
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    
    # Verifica código
    c.execute('''SELECT id, dias, ativo FROM codigos WHERE codigo = ?''', (codigo,))
    result = c.fetchone()
    
    if not result:
        await update.message.reply_text("❌ Código inválido!")
        conn.close()
        return
    
    codigo_id, dias, ativo = result
    
    if not ativo:
        await update.message.reply_text("❌ Este código já foi usado!")
        conn.close()
        return
    
    # Calcula expiração
    if dias:
        expiracao = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d")
    else:
        expiracao = None  # Vitalício
    
    # Registra usuário
    nome = update.effective_user.first_name or "Cliente"
    c.execute('''INSERT OR REPLACE INTO usuarios 
                 (telegram_id, nome, tipo, plano, data_expiracao, ativo)
                 VALUES (?, ?, ?, ?, ?, 1)''',
              (user_id, nome, 'cliente', f"{dias or 'Vitalício'} dias", expiracao))
    
    # Marca código como usado
    c.execute('''UPDATE codigos SET usado_por = ?, data_uso = ?, ativo = 0 
                 WHERE id = ?''', (user_id, datetime.now(), codigo_id))
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text(
        f"🎉 *Acesso Liberado!*\n\n"
        f"✅ Código válido!\n"
        f"⏳ Período: {dias or 'Vitalício'} dias\n\n"
        f"Comece a usar o bot agora mesmo!\n"
        f"/start para iniciar",
        parse_mode='Markdown'
    )

# ==================== SISTEMA DE PRODUTOS ====================

async def produtos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gerenciar produtos"""
    user_id = update.effective_user.id
    
    if not verificar_acesso(user_id):
        await update.message.reply_text("❌ Acesso negado! Use /usar [CÓDIGO]")
        return
    
    keyboard = [
        [InlineKeyboardButton("➕ Cadastrar Produto", callback_data="add_produto")],
        [InlineKeyboardButton("📋 Listar Produtos", callback_data="listar_produtos")],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📦 *GERENCIAR PRODUTOS*\n\n"
        "Escolha uma opção:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def processar_produtos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa ações de produtos"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if not verificar_acesso(user_id):
        await query.edit_message_text("❌ Acesso negado!")
        return
    
    if query.data == "add_produto":
        context.user_data['acao'] = 'add_produto'
        await query.edit_message_text(
            "📦 *Cadastrar Novo Produto*\n\n"
            "Envie no formato:\n"
            "`Nome do Produto - R$ 00,00`\n\n"
            "Exemplo: Corte de Cabelo - R$ 30,00",
            parse_mode='Markdown'
        )
    
    elif query.data == "listar_produtos":
        conn = sqlite3.connect('sistema.db')
        c = conn.cursor()
        c.execute('''SELECT id, nome, preco FROM produtos 
                     WHERE user_id = ? AND ativo = 1''', (user_id,))
        produtos = c.fetchall()
        conn.close()
        
        if not produtos:
            await query.edit_message_text("📦 Nenhum produto cadastrado.")
            return
        
        texto = "📋 *SEUS PRODUTOS*\n\n"
        for pid, nome, preco in produtos:
            texto += f"📌 {nome}\n"
            texto += f"💰 R$ {preco:.2f}\n"
            texto += "─" * 20 + "\n"
        
        await query.edit_message_text(texto, parse_mode='Markdown')

async def registrar_produto_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registra produto via texto"""
    if 'acao' not in context.user_data:
        return
    
    if context.user_data['acao'] == 'add_produto':
        texto = update.message.text
        user_id = update.effective_user.id
        
        # Tenta extrair nome e preço
        match = re.search(r'(.+?)[-–—]?\s*R?\$?\s*(\d+(?:[.,]\d+)?)', texto, re.IGNORECASE)
        
        if match:
            nome = match.group(1).strip()
            preco = float(match.group(2).replace(',', '.'))
            
            conn = sqlite3.connect('sistema.db')
            c = conn.cursor()
            c.execute('''INSERT INTO produtos (user_id, nome, preco)
                         VALUES (?, ?, ?)''', (user_id, nome, preco))
            conn.commit()
            conn.close()
            
            del context.user_data['acao']
            
            await update.message.reply_text(
                f"✅ *Produto cadastrado!*\n\n"
                f"📌 {nome}\n"
                f"💰 R$ {preco:.2f}",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "❌ Formato inválido!\n"
                "Use: Nome do Produto - R$ 30,00"
            )

# ==================== SISTEMA DE VENDAS ====================

async def venda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registra uma venda"""
    user_id = update.effective_user.id
    texto = update.message.text.lower()
    
    # Primeiro, verifica se é um produto conhecido
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    c.execute('''SELECT id, nome, preco FROM produtos 
                 WHERE user_id = ? AND ativo = 1''', (user_id,))
    produtos = c.fetchall()
    conn.close()
    
    produto_encontrado = None
    quantidade = 1
    cliente = "cliente"
    
    # Tenta encontrar o produto no texto
    for pid, pnome, ppreco in produtos:
        if pnome.lower() in texto:
            produto_encontrado = (pid, pnome, ppreco)
            break
    
    if not produto_encontrado:
        return False  # Não é venda
    
    # Tenta extrair quantidade
    qtd_match = re.search(r'(\d+)\s*(?:x|unidades?|un|vezes?)', texto)
    if qtd_match:
        quantidade = int(qtd_match.group(1))
    
    # Tenta extrair nome do cliente
    palavras = texto.split()
    if 'para' in palavras:
        idx = palavras.index('para')
        if idx + 1 < len(palavras):
            cliente = palavras[idx + 1]
    elif 'do' in palavras:
        idx = palavras.index('do')
        if idx + 1 < len(palavras):
            cliente = palavras[idx + 1]
    
    pid, pnome, ppreco = produto_encontrado
    valor_total = ppreco * quantidade
    
    # Registra venda
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    c.execute('''INSERT INTO vendas 
                 (user_id, produto_id, produto_nome, cliente_nome, valor, quantidade, data)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (user_id, pid, pnome, cliente, valor_total, quantidade, datetime.now()))
    conn.commit()
    conn.close()
    
    await update.message.reply_text(
        f"✅ *VENDA REGISTRADA*\n\n"
        f"📦 {quantidade}x {pnome}\n"
        f"👤 Cliente: {cliente}\n"
        f"💰 Total: R$ {valor_total:.2f}",
        parse_mode='Markdown'
    )
    
    return True

# ==================== RELATÓRIOS ====================

async def relatorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Relatório de vendas"""
    user_id = update.effective_user.id
    
    if not verificar_acesso(user_id):
        await update.message.reply_text("❌ Acesso negado!")
        return
    
    keyboard = [
        [InlineKeyboardButton("📊 Vendas Hoje", callback_data="rel_hoje")],
        [InlineKeyboardButton("📊 Vendas Semana", callback_data="rel_semana")],
        [InlineKeyboardButton("📊 Vendas Mês", callback_data="rel_mes")],
        [InlineKeyboardButton("💰 Gastos x Ganhos", callback_data="rel_financeiro")],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📈 *RELATÓRIOS*\n\n"
        "Escolha o tipo:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def processar_relatorios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa relatórios"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    hoje = datetime.now()
    
    if query.data == "rel_hoje":
        data_inicio = hoje.strftime("%Y-%m-%d")
        titulo = "HOJE"
    elif query.data == "rel_semana":
        inicio_semana = hoje - timedelta(days=hoje.weekday())
        data_inicio = inicio_semana.strftime("%Y-%m-%d")
        titulo = "SEMANA"
    elif query.data == "rel_mes":
        data_inicio = hoje.replace(day=1).strftime("%Y-%m-%d")
        titulo = "MÊS"
    else:
        return
    
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    
    # Vendas do período
    c.execute('''SELECT produto_nome, cliente_nome, quantidade, valor, data 
                 FROM vendas WHERE user_id = ? AND date(data) >= ?
                 ORDER BY data DESC''', (user_id, data_inicio))
    vendas = c.fetchall()
    
    # Totais
    c.execute('''SELECT SUM(valor), COUNT(*) FROM vendas 
                 WHERE user_id = ? AND date(data) >= ?''', (user_id, data_inicio))
    total_valor, total_vendas = c.fetchone()
    
    conn.close()
    
    if not vendas:
        await query.edit_message_text(f"📊 Nenhuma venda em {titulo.lower()}.")
        return
    
    texto = f"📈 *VENDAS {titulo}*\n\n"
    texto += f"💰 Total: R$ {total_valor:.2f}\n"
    texto += f"📦 Vendas: {total_vendas}\n\n"
    texto += "📋 *Detalhado:*\n"
    
    for prod, cliente, qtd, valor, data in vendas[:10]:
        texto += f"🕐 {data[11:16]} - {qtd}x {prod}\n"
        texto += f"└─ 👤 {cliente} - R$ {valor:.2f}\n\n"
    
    await query.edit_message_text(texto, parse_mode='Markdown')

# ==================== ÁUDIO ====================

async def processar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa áudio e converte para texto"""
    user_id = update.effective_user.id
    
    if not verificar_acesso(user_id):
        await update.message.reply_text("❌ Acesso negado!")
        return
    
    await update.message.reply_text("🎤 Processando áudio... aguarde...")
    
    try:
        # Baixa o áudio
        arquivo = await update.message.voice.get_file()
        
        # Cria arquivo temporário
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
            await arquivo.download_to_drive(tmp_ogg.name)
            ogg_path = tmp_ogg.name
        
        # Converte para wav
        wav_path = ogg_path.replace('.ogg', '.wav')
        audio = AudioSegment.from_ogg(ogg_path)
        audio.export(wav_path, format="wav")
        
        # Reconhece fala
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            texto = recognizer.recognize_google(audio_data, language='pt-BR')
        
        # Limpa arquivos temporários
        os.unlink(ogg_path)
        os.unlink(wav_path)
        
        await update.message.reply_text(f"📝 *Texto reconhecido:*\n{texto}", parse_mode='Markdown')
        
        # Processa o texto como se fosse uma mensagem normal
        update.message.text = texto
        await registrar_mensagem(update, context)
        
    except Exception as e:
        await update.message.reply_text("❌ Não consegui entender o áudio. Tente falar mais claramente.")

# ==================== REGISTRAR MENSAGEM ====================

async def registrar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registra qualquer mensagem (gasto, ganho ou venda)"""
    user_id = update.effective_user.id
    
    if not verificar_acesso(user_id) and user_id != ADMIN_ID:
        return
    
    texto = update.message.text.lower()
    
    # Tenta registrar como venda primeiro
    if await venda(update, context):
        return
    
    # Se não for venda, verifica se é gasto ou ganho
    if any(p in texto for p in ['gastei', 'gasto', 'paguei', 'comprei']):
        tipo = 'gasto'
    elif any(p in texto for p in ['ganhei', 'recebi']):
        tipo = 'ganho'
    else:
        return
    
    # Extrai valor
    valores = re.findall(r'(\d+(?:[.,]\d+)?)', texto)
    if not valores:
        await update.message.reply_text("❌ Não consegui identificar o valor!")
        return
    
    valor = float(valores[0].replace(',', '.'))
    descricao = texto
    for palavra in ['gastei', 'ganhei', 'recebi', 'paguei', 'comprei', 'em', 'de', 'do', 'da']:
        descricao = descricao.replace(palavra, '')
    descricao = descricao.replace(valores[0], '').strip()
    
    if not descricao:
        descricao = 'sem descrição'
    
    # Registra
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    c.execute('''INSERT INTO transacoes (user_id, tipo, descricao, valor, data)
                 VALUES (?, ?, ?, ?, ?)''',
              (user_id, tipo, descricao, valor, datetime.now()))
    conn.commit()
    conn.close()
    
    emoji = '💰' if tipo == 'gasto' else '💵'
    await update.message.reply_text(
        f"{emoji} *Registrado!*\n\n"
        f"{'Gasto' if tipo == 'gasto' else 'Ganho'}: R$ {valor:.2f}\n"
        f"📝 {descricao}",
        parse_mode='Markdown'
    )

# ==================== COMANDOS BÁSICOS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensagem inicial"""
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        await update.message.reply_text(
            "👑 *PAINEL ADMIN*\n\n"
            "Comandos disponíveis:\n"
            "/codigos - Gerar códigos de acesso\n"
            "/produtos - Gerenciar produtos\n"
            "/relatorio - Ver relatórios\n"
            "/hoje - Resumo de hoje\n\n"
            "📝 *Exemplos de uso:*\n"
            "• 'corte para João' (vende produto)\n"
            "• 'gastei 20 em pizza' (registra gasto)\n"
            "• 'ganhei 100 do Paulo' (registra ganho)\n"
            "• Envie ÁUDIO com qualquer comando!",
            parse_mode='Markdown'
        )
    elif verificar_acesso(user_id):
        await update.message.reply_text(
            "👋 *Bem-vindo!*\n\n"
            "📦 *Para vender:* 'corte para João'\n"
            "💰 *Para gastos:* 'gastei 20 em almoço'\n"
            "💵 *Para ganhos:* 'ganhei 100 do Paulo'\n"
            "🎤 *Envie áudios também!*\n\n"
            "Comandos:\n"
            "/produtos - Cadastrar produtos\n"
            "/relatorio - Ver vendas\n"
            "/hoje - Resumo do dia",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"👋 *Assistente Financeiro*\n\n"
            f"Para usar, você precisa de um código de acesso.\n"
            f"Use: /usar [CÓDIGO]\n\n"
            f"💬 Contato: {CONTATO}",
            parse_mode='Markdown'
        )

async def hoje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resumo de hoje"""
    user_id = update.effective_user.id
    
    if not verificar_acesso(user_id):
        return
    
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    
    hoje = datetime.now().strftime("%Y-%m-%d")
    
    # Gastos
    c.execute('''SELECT SUM(valor) FROM transacoes 
                 WHERE user_id = ? AND tipo = 'gasto' AND date(data) = ?''', (user_id, hoje))
    gastos = c.fetchone()[0] or 0
    
    # Ganhos
    c.execute('''SELECT SUM(valor) FROM transacoes 
                 WHERE user_id = ? AND tipo = 'ganho' AND date(data) = ?''', (user_id, hoje))
    ganhos = c.fetchone()[0] or 0
    
    # Vendas
    c.execute('''SELECT SUM(valor), COUNT(*) FROM vendas 
                 WHERE user_id = ? AND date(data) = ?''', (user_id, hoje))
    venda_valor, venda_qtd = c.fetchone()
    venda_valor = venda_valor or 0
    venda_qtd = venda_qtd or 0
    
    conn.close()
    
    saldo = (ganhos + venda_valor) - gastos
    
    await update.message.reply_text(
        f"📊 *RESUMO DE HOJE*\n\n"
        f"💰 Gastos: R$ {gastos:.2f}\n"
        f"💵 Ganhos: R$ {ganhos:.2f}\n"
        f"🛒 Vendas: R$ {venda_valor:.2f} ({venda_qtd} vendas)\n"
        f"💸 Saldo: R$ {saldo:.2f}",
        parse_mode='Markdown'
    )

# ==================== MAIN ====================

def main():
    # Inicia banco
    init_db()
    
    # Cria aplicação
    app = Application.builder().token(TOKEN).build()
    
    # Comandos públicos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("usar", usar_codigo))
    app.add_handler(CommandHandler("hoje", hoje))
    app.add_handler(CommandHandler("semana", hoje))
    app.add_handler(CommandHandler("mes", hoje))
    app.add_handler(CommandHandler("produtos", produtos))
    app.add_handler(CommandHandler("relatorio", relatorio))
    
    # Comandos admin
    app.add_handler(CommandHandler("codigos", codigos))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(processar_codigos, pattern="^codigo_|^listar_codigos"))
    app.add_handler(CallbackQueryHandler(processar_produtos, pattern="^add_produto|^listar_produtos"))
    app.add_handler(CallbackQueryHandler(processar_relatorios, pattern="^rel_"))
    
    # Mensagens de texto
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registrar_produto_texto), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registrar_mensagem), group=2)
    
    # Áudio
    app.add_handler(MessageHandler(filters.VOICE, processar_audio))
    
    print("=" * 50)
    print("🤖 BOT INICIADO COM SUCESSO!")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"📞 Contato: {CONTATO}")
    print("=" * 50)
    
    app.run_polling()

if __name__ == "__main__":
    main()