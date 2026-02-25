import os
import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import re
import random
import string
import speech_recognition as sr
from pydub import AudioSegment
import tempfile
import threading
from flask import Flask

# ==================== CONFIGURAÇÕES ====================
TOKEN = "8778081445:AAF8PEnPHntpnN3wjqNGAfTzWNPhJV_4VxM"  # COLE SEU TOKEN AQUI
ADMIN_ID = 5052937721  # COLE SEU ID AQUI
CONTATO = "@jeffinhooliveira"  # SEU CONTATO

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
                  ativo INTEGER DEFAULT 1)''')
    
    # Vendas
    c.execute('''CREATE TABLE IF NOT EXISTS vendas
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  produto_nome TEXT,
                  cliente_nome TEXT,
                  valor REAL,
                  quantidade INTEGER DEFAULT 1,
                  data TEXT,
                  pago INTEGER DEFAULT 1)''')
    
    # Transações (gastos/ganhos)
    c.execute('''CREATE TABLE IF NOT EXISTS transacoes
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  tipo TEXT,
                  descricao TEXT,
                  valor REAL,
                  data TEXT,
                  pessoa TEXT)''')
    
    # DÍVIDAS - NOVA TABELA!
    c.execute('''CREATE TABLE IF NOT EXISTS dividas
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  pessoa TEXT,
                  valor REAL,
                  motivo TEXT,
                  data_criacao TEXT,
                  data_vencimento TEXT,
                  status TEXT DEFAULT 'pendente')''')
    
    # Pagamentos de dívidas
    c.execute('''CREATE TABLE IF NOT EXISTS pagamentos_dividas
                 (id INTEGER PRIMARY KEY,
                  divida_id INTEGER,
                  valor REAL,
                  data TEXT,
                  observacao TEXT)''')
    
    conn.commit()
    conn.close()

# ==================== FUNÇÕES AUXILIARES ====================

def gerar_codigo(tamanho=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=tamanho))

def verificar_acesso(user_id):
    if user_id == ADMIN_ID:
        return True
    
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    c.execute("SELECT ativo, data_expiracao FROM usuarios WHERE telegram_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result and result[0] == 1:
        if result[1]:
            try:
                expiracao = datetime.strptime(result[1], "%Y-%m-%d")
                if expiracao > datetime.now():
                    return True
            except:
                return True
        else:
            return True
    return False

def extrair_valor(texto):
    """Extrai valor numérico do texto"""
    valores = re.findall(r'(\d+(?:[.,]\d+)?)', texto)
    if valores:
        return float(valores[0].replace(',', '.'))
    return None

def extrair_pessoa(texto):
    """Extrai nome de pessoa do texto"""
    # Lista de palavras que podem indicar uma pessoa
    indicadores = ['para', 'do', 'da', 'de', 'com', 'jefferson', 'paulo', 'joão', 'maria', 'jose', 'ana', 'carlos']
    
    palavras = texto.lower().split()
    for i, palavra in enumerate(palavras):
        if palavra in indicadores and i + 1 < len(palavras):
            return palavras[i + 1].capitalize()
    
    # Se encontrar nome próprio (começa com maiúscula no original)
    for palavra in texto.split():
        if palavra[0].isupper() and len(palavra) > 2:
            return palavra
    
    return None

# ==================== SISTEMA DE DÍVIDAS ====================

async def processar_divida(texto, user_id):
    """Processa comandos relacionados a dívidas"""
    texto_lower = texto.lower()
    
    # Verificar se é sobre dívida
    if 'devendo' in texto_lower or 'divida' in texto_lower or 'deve' in texto_lower:
        pessoa = extrair_pessoa(texto)
        valor = extrair_valor(texto)
        
        if pessoa and valor:
            # Registrar nova dívida
            motivo = texto
            for p in ['devendo', 'divida', 'deve', 'ficou', 'me', str(valor).replace('.', ',')]:
                motivo = motivo.lower().replace(p, '')
            motivo = motivo.strip()
            
            conn = sqlite3.connect('sistema.db')
            c = conn.cursor()
            c.execute('''INSERT INTO dividas (user_id, pessoa, valor, motivo, data_criacao, status)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (user_id, pessoa, valor, motivo, datetime.now(), 'pendente'))
            conn.commit()
            conn.close()
            
            return f"✅ *Dívida registrada!*\n\n👤 {pessoa}\n💰 R$ {valor:.2f}\n📝 {motivo}"
    
    # Verificar pagamento de dívida
    elif 'pagou' in texto_lower or 'quitou' in texto_lower or 'recebi' in texto_lower:
        pessoa = extrair_pessoa(texto)
        valor = extrair_valor(texto)
        
        if pessoa:
            conn = sqlite3.connect('sistema.db')
            c = conn.cursor()
            
            # Buscar dívidas ativas da pessoa
            c.execute('''SELECT id, valor FROM dividas 
                         WHERE user_id = ? AND pessoa = ? AND status = 'pendente'
                         ORDER BY data_criacao''', (user_id, pessoa))
            dividas = c.fetchall()
            
            if not dividas:
                conn.close()
                return f"❌ Nenhuma dívida encontrada para {pessoa}"
            
            if valor:
                # Pagamento parcial ou total
                valor_pago = valor
                restante = valor_pago
                
                for divida_id, valor_divida in dividas:
                    if restante <= 0:
                        break
                    
                    if restante >= valor_divida:
                        # Quitar dívida inteira
                        c.execute('''UPDATE dividas SET status = 'quitada' WHERE id = ?''', (divida_id,))
                        c.execute('''INSERT INTO pagamentos_dividas (divida_id, valor, data)
                                     VALUES (?, ?, ?)''', (divida_id, valor_divida, datetime.now()))
                        restante -= valor_divida
                    else:
                        # Pagamento parcial
                        novo_valor = valor_divida - restante
                        c.execute('''UPDATE dividas SET valor = ? WHERE id = ?''', (novo_valor, divida_id))
                        c.execute('''INSERT INTO pagamentos_dividas (divida_id, valor, data)
                                     VALUES (?, ?, ?)''', (divida_id, restante, datetime.now()))
                        restante = 0
                
                conn.commit()
                
                # Verificar se ainda tem dívidas
                c.execute('''SELECT SUM(valor) FROM dividas 
                             WHERE user_id = ? AND pessoa = ? AND status = 'pendente'''', (user_id, pessoa))
                saldo_restante = c.fetchone()[0] or 0
                
                conn.close()
                
                if saldo_restante == 0:
                    return f"✅ *Dívida de {pessoa} quitada!* 💰 R$ {valor_pago:.2f} recebidos"
                else:
                    return f"✅ *Pagamento registrado!*\n\n👤 {pessoa}\n💰 Pago: R$ {valor_pago:.2f}\n💸 Restante: R$ {saldo_restante:.2f}"
            else:
                conn.close()
                return f"❌ Informe o valor pago. Ex: '{pessoa} pagou 50 reais'"
    
    return None

async def consultar_dividas(update, pessoa=None):
    """Consulta dívidas"""
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    
    if pessoa:
        c.execute('''SELECT pessoa, SUM(valor), COUNT(*) FROM dividas 
                     WHERE user_id = ? AND pessoa = ? AND status = 'pendente'
                     GROUP BY pessoa''', (user_id, pessoa))
    else:
        c.execute('''SELECT pessoa, SUM(valor), COUNT(*) FROM dividas 
                     WHERE user_id = ? AND status = 'pendente'
                     GROUP BY pessoa ORDER BY SUM(valor) DESC''', (user_id,))
    
    dividas = c.fetchall()
    conn.close()
    
    if not dividas:
        if pessoa:
            return f"✅ {pessoa} não tem dívidas pendentes!"
        else:
            return "✅ Nenhuma dívida pendente!"
    
    if pessoa:
        total = dividas[0][1]
        qtd = dividas[0][2]
        return f"📊 *Dívidas de {pessoa}*\n\n💰 Total: R$ {total:.2f}\n📦 {qtd} dívida(s)"
    else:
        texto = "📊 *TODAS AS DÍVIDAS*\n\n"
        for pes, val, qtd in dividas:
            texto += f"👤 *{pes}*\n"
            texto += f"├─ 💰 R$ {val:.2f}\n"
            texto += f"└─ 📦 {qtd} dívida(s)\n\n"
        return texto

# ==================== SISTEMA DE VENDAS ====================

async def processar_venda(texto, user_id):
    """Processa venda"""
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    
    # Buscar produtos do usuário
    c.execute('''SELECT nome, preco FROM produtos WHERE user_id = ? AND ativo = 1''', (user_id,))
    produtos = c.fetchall()
    conn.close()
    
    texto_lower = texto.lower()
    
    for prod_nome, prod_preco in produtos:
        if prod_nome.lower() in texto_lower:
            # Encontrou produto
            quantidade = 1
            qtd_match = re.search(r'(\d+)\s*(?:x|unidades?|un|vezes?)', texto_lower)
            if qtd_match:
                quantidade = int(qtd_match.group(1))
            
            # Extrair cliente
            cliente = "cliente"
            palavras = texto.split()
            for i, palavra in enumerate(palavras):
                if palavra.lower() in ['para', 'do', 'da', 'de'] and i + 1 < len(palavras):
                    cliente = palavras[i + 1]
                    break
            
            valor_total = prod_preco * quantidade
            
            # Verificar se é pra pagar depois (dívida)
            if 'fiado' in texto_lower or 'deve' in texto_lower or 'depois' in texto_lower:
                # Registrar como dívida
                conn = sqlite3.connect('sistema.db')
                c = conn.cursor()
                c.execute('''INSERT INTO dividas (user_id, pessoa, valor, motivo, data_criacao, status)
                             VALUES (?, ?, ?, ?, ?, ?)''',
                          (user_id, cliente, valor_total, f"{quantidade}x {prod_nome}", datetime.now(), 'pendente'))
                conn.commit()
                conn.close()
                
                return f"📝 *Venda fiado registrada!*\n\n📦 {quantidade}x {prod_nome}\n👤 Cliente: {cliente}\n💰 R$ {valor_total:.2f}\n⏳ *Aguardando pagamento*"
            else:
                # Registrar venda normal
                conn = sqlite3.connect('sistema.db')
                c = conn.cursor()
                c.execute('''INSERT INTO vendas (user_id, produto_nome, cliente_nome, valor, quantidade, data, pago)
                             VALUES (?, ?, ?, ?, ?, ?, ?)''',
                          (user_id, prod_nome, cliente, valor_total, quantidade, datetime.now(), 1))
                conn.commit()
                conn.close()
                
                return f"✅ *VENDA REALIZADA!*\n\n📦 {quantidade}x {prod_nome}\n👤 Cliente: {cliente}\n💰 Total: R$ {valor_total:.2f}"
    
    return None

# ==================== PROCESSAMENTO PRINCIPAL ====================

async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa qualquer mensagem"""
    user_id = update.effective_user.id
    
    if not verificar_acesso(user_id) and user_id != ADMIN_ID:
        await update.message.reply_text(
            f"❌ Acesso negado!\n\nContato: {CONTATO}",
            parse_mode='Markdown'
        )
        return
    
    texto = update.message.text
    texto_lower = texto.lower()
    
    # ===== CONSULTAS =====
    if any(p in texto_lower for p in ['quanto', 'saldo', 'divida', 'devendo']):
        # Consultar dívidas específicas
        if any(p in texto_lower for p in ['jefferson', 'paulo', 'joão', 'maria', 'jose', 'carlos', 'ana']):
            pessoa = extrair_pessoa(texto)
            if pessoa:
                resposta = await consultar_dividas(update, pessoa)
                await update.message.reply_text(resposta, parse_mode='Markdown')
                return
        else:
            # Todas as dívidas
            resposta = await consultar_dividas(update)
            await update.message.reply_text(resposta, parse_mode='Markdown')
            return
    
    # ===== DÍVIDAS =====
    resposta_divida = await processar_divida(texto, user_id)
    if resposta_divida:
        await update.message.reply_text(resposta_divida, parse_mode='Markdown')
        return
    
    # ===== VENDAS =====
    resposta_venda = await processar_venda(texto, user_id)
    if resposta_venda:
        await update.message.reply_text(resposta_venda, parse_mode='Markdown')
        return
    
    # ===== GASTOS =====
    if any(p in texto_lower for p in ['gastei', 'gasto', 'paguei', 'comprei']):
        valor = extrair_valor(texto)
        if valor:
            descricao = texto_lower
            for p in ['gastei', 'gasto', 'paguei', 'comprei', 'em', str(valor).replace('.', ','), 'r$', 'reais']:
                descricao = descricao.replace(p, '')
            descricao = descricao.strip()
            
            if not descricao:
                descricao = 'sem descrição'
            
            conn = sqlite3.connect('sistema.db')
            c = conn.cursor()
            c.execute('''INSERT INTO transacoes (user_id, tipo, descricao, valor, data)
                         VALUES (?, ?, ?, ?, ?)''',
                      (user_id, 'gasto', descricao.capitalize(), valor, datetime.now()))
            conn.commit()
            conn.close()
            
            await update.message.reply_text(
                f"💰 *Gasto registrado!*\n\nR$ {valor:.2f}\n📝 {descricao.capitalize()}",
                parse_mode='Markdown'
            )
            return
        else:
            await update.message.reply_text("❌ Informe o valor! Ex: 'gastei 50 em lanche'")
            return
    
    # ===== GANHOS =====
    if any(p in texto_lower for p in ['ganhei', 'recebi']):
        valor = extrair_valor(texto)
        if valor:
            descricao = texto_lower
            for p in ['ganhei', 'recebi', str(valor).replace('.', ','), 'r$', 'reais']:
                descricao = descricao.replace(p, '')
            descricao = descricao.strip()
            
            if not descricao:
                descricao = 'sem descrição'
            
            conn = sqlite3.connect('sistema.db')
            c = conn.cursor()
            c.execute('''INSERT INTO transacoes (user_id, tipo, descricao, valor, data)
                         VALUES (?, ?, ?, ?, ?)''',
                      (user_id, 'ganho', descricao.capitalize(), valor, datetime.now()))
            conn.commit()
            conn.close()
            
            await update.message.reply_text(
                f"💵 *Ganho registrado!*\n\nR$ {valor:.2f}\n📝 {descricao.capitalize()}",
                parse_mode='Markdown'
            )
            return
        else:
            await update.message.reply_text("❌ Informe o valor! Ex: 'ganhei 100 do Paulo'")
            return
    
    # Se não entendeu nada
    await update.message.reply_text(
        "❓ *Não entendi*\n\n"
        "Exemplos:\n"
        "• 'jefferson ficou me devendo 50 reais'\n"
        "• 'quanto jefferson me deve'\n"
        "• 'jefferson pagou 30 reais'\n"
        "• 'corte para joão' (venda)\n"
        "• 'gastei 50 em lanche'\n"
        "• 'ganhei 100 do paulo'",
        parse_mode='Markdown'
    )

# ==================== ÁUDIO ====================

async def processar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa áudio"""
    user_id = update.effective_user.id
    
    if not verificar_acesso(user_id) and user_id != ADMIN_ID:
        return
    
    await update.message.reply_text("🎤 Processando áudio...")
    
    try:
        # Baixar áudio
        arquivo = await update.message.voice.get_file()
        
        # Salvar temporariamente
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
            await arquivo.download_to_drive(tmp_ogg.name)
            ogg_path = tmp_ogg.name
        
        # Converter para wav
        wav_path = ogg_path.replace('.ogg', '.wav')
        audio = AudioSegment.from_ogg(ogg_path)
        audio.export(wav_path, format="wav")
        
        # Reconhecer fala
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            texto = recognizer.recognize_google(audio_data, language='pt-BR')
        
        # Limpar arquivos
        os.unlink(ogg_path)
        os.unlink(wav_path)
        
        await update.message.reply_text(f"📝 *Você disse:*\n{texto}", parse_mode='Markdown')
        
        # Processar o texto
        update.message.text = texto
        await processar_mensagem(update, context)
        
    except Exception as e:
        await update.message.reply_text("❌ Não consegui entender. Fale mais claramente ou use texto.")

# ==================== COMANDOS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensagem inicial"""
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        await update.message.reply_text(
            "👑 *PAINEL ADMIN*\n\n"
            "Comandos:\n"
            "/codigos - Gerar códigos\n"
            "/produtos - Cadastrar produtos\n"
            "/dividas - Ver todas dívidas\n"
            "/hoje - Resumo do dia\n\n"
            "💡 *Exemplos:*\n"
            "• 'joão ficou devendo 50 do lanche'\n"
            "• 'quanto joão deve'\n"
            "• 'joão pagou 30'\n"
            "• 'corte para maria'\n"
            "• 'gastei 20 em pizza'",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "👋 *Olá!*\n\n"
            "💡 *Exemplos:*\n"
            "• 'joão ficou devendo 50 reais'\n"
            "• 'quanto joão me deve'\n"
            "• 'joão pagou 30 reais'\n"
            "• 'corte para maria'\n"
            "• 'gastei 50 em almoço'",
            parse_mode='Markdown'
        )

async def produtos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gerenciar produtos"""
    user_id = update.effective_user.id
    
    if not verificar_acesso(user_id):
        return
    
    keyboard = [
        [InlineKeyboardButton("➕ Cadastrar", callback_data="add_produto")],
        [InlineKeyboardButton("📋 Listar", callback_data="listar_produtos")],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📦 *GERENCIAR PRODUTOS*",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def codigos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gerar códigos (admin)"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    keyboard = [
        [InlineKeyboardButton("🎫 7 dias", callback_data="codigo_7")],
        [InlineKeyboardButton("🎫 15 dias", callback_data="codigo_15")],
        [InlineKeyboardButton("🎫 30 dias", callback_data="codigo_30")],
        [InlineKeyboardButton("🎫 Vitalício", callback_data="codigo_vitalicio")],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎫 *GERAR CÓDIGOS*",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def hoje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resumo do dia"""
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
    venda = c.fetchone()
    venda_valor = venda[0] or 0
    venda_qtd = venda[1] or 0
    
    # Dívidas recebidas hoje
    c.execute('''SELECT SUM(valor) FROM pagamentos_dividas WHERE date(data) = ?''', (hoje,))
    dividas_pagas = c.fetchone()[0] or 0
    
    conn.close()
    
    total_ganhos = ganhos + venda_valor + dividas_pagas
    saldo = total_ganhos - gastos
    
    await update.message.reply_text(
        f"📊 *RESUMO DE HOJE*\n\n"
        f"💰 Gastos: R$ {gastos:.2f}\n"
        f"💵 Ganhos: R$ {ganhos:.2f}\n"
        f"🛒 Vendas: R$ {venda_valor:.2f} ({venda_qtd})\n"
        f"💳 Dívidas pagas: R$ {dividas_pagas:.2f}\n"
        f"💸 Saldo do dia: R$ {saldo:.2f}",
        parse_mode='Markdown'
    )

async def dividas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ver todas dívidas"""
    resposta = await consultar_dividas(update)
    await update.message.reply_text(resposta, parse_mode='Markdown')

async def usar_codigo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usar código de acesso"""
    if not context.args:
        await update.message.reply_text("Use: /usar [CÓDIGO]")
        return
    
    codigo = context.args[0].upper()
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('sistema.db')
    c = conn.cursor()
    
    c.execute('''SELECT id, dias, ativo FROM codigos WHERE codigo = ?''', (codigo,))
    result = c.fetchone()
    
    if not result:
        await update.message.reply_text("❌ Código inválido!")
        conn.close()
        return
    
    codigo_id, dias, ativo = result
    
    if not ativo:
        await update.message.reply_text("❌ Código já usado!")
        conn.close()
        return
    
    expiracao = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d") if dias else None
    nome = update.effective_user.first_name or "Cliente"
    
    c.execute('''INSERT OR REPLACE INTO usuarios 
                 (telegram_id, nome, tipo, plano, data_expiracao, ativo)
                 VALUES (?, ?, ?, ?, ?, 1)''',
              (user_id, nome, 'cliente', f"{dias or 'Vitalício'} dias", expiracao))
    
    c.execute('''UPDATE codigos SET usado_por = ?, data_uso = ?, ativo = 0 
                 WHERE id = ?''', (user_id, datetime.now(), codigo_id))
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text(
        f"🎉 *Acesso Liberado!*\n\n"
        f"⏳ {dias or 'Vitalício'} dias\n"
        f"✅ Comece a usar!",
        parse_mode='Markdown'
    )

# ==================== CALLBACKS ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa callbacks dos botões"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data.startswith('codigo_'):
        if user_id != ADMIN_ID:
            return
        
        dias_map = {
            'codigo_7': 7,
            'codigo_15': 15,
            'codigo_30': 30,
            'codigo_vitalicio': None
        }
        
        dias = dias_map.get(query.data)
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
            f"✅ *Código gerado!*\n\n"
            f"`{codigo}`\n"
            f"⏳ {tipo}",
            parse_mode='Markdown'
        )
    
    elif query.data == "add_produto":
        context.user_data['acao'] = 'add_produto'
        await query.edit_message_text(
            "📦 *Envie o produto:*\n"
            "`Nome - Preço`\n"
            "Ex: Corte de Cabelo - 30",
            parse_mode='Markdown'
        )
    
    elif query.data == "listar_produtos":
        conn = sqlite3.connect('sistema.db')
        c = conn.cursor()
        c.execute('''SELECT nome, preco FROM produtos 
                     WHERE user_id = ? AND ativo = 1''', (user_id,))
        produtos = c.fetchall()
        conn.close()
        
        if not produtos:
            await query.edit_message_text("📦 Nenhum produto cadastrado.")
            return
        
        texto = "📋 *PRODUTOS*\n\n"
        for nome, preco in produtos:
            texto += f"📌 {nome}: R$ {preco:.2f}\n"
        
        await query.edit_message_text(texto, parse_mode='Markdown')

async def registrar_produto_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registra produto via texto"""
    if 'acao' not in context.user_data or context.user_data['acao'] != 'add_produto':
        return
    
    texto = update.message.text
    match = re.search(r'(.+?)[-–—]?\s*R?\$?\s*(\d+(?:[.,]\d+)?)', texto, re.IGNORECASE)
    
    if match:
        nome = match.group(1).strip()
        preco = float(match.group(2).replace(',', '.'))
        
        conn = sqlite3.connect('sistema.db')
        c = conn.cursor()
        c.execute('''INSERT INTO produtos (user_id, nome, preco)
                     VALUES (?, ?, ?)''', (update.effective_user.id, nome, preco))
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
        await update.message.reply_text("❌ Formato inválido! Use: Nome - 30")

# ==================== SERVIDOR WEB ====================
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "🤖 Bot Financeiro Rodando 24/7!"

def run_web():
    app_web.run(host='0.0.0.0', port=8080)

# ==================== MAIN ====================
def main():
    # Iniciar banco
    init_db()
    
    # Iniciar servidor web em background
    threading.Thread(target=run_web, daemon=True).start()
    
    # Criar bot
    app = Application.builder().token(TOKEN).build()
    
    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("usar", usar_codigo))
    app.add_handler(CommandHandler("codigos", codigos))
    app.add_handler(CommandHandler("produtos", produtos))
    app.add_handler(CommandHandler("dividas", dividas))
    app.add_handler(CommandHandler("hoje", hoje))
    app.add_handler(CommandHandler("semana", hoje))
    app.add_handler(CommandHandler("mes", hoje))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # Mensagens
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registrar_produto_texto), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem), group=2)
    app.add_handler(MessageHandler(filters.VOICE, processar_audio))
    
    print("="*50)
    print("🤖 BOT INICIADO COM SUCESSO!")
    print(f"👑 Admin: {ADMIN_ID}")
    print("="*50)
    
    app.run_polling()

if __name__ == "__main__":
    main()
