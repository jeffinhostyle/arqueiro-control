import os
import sqlite3
import re
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import speech_recognition as sr
from pydub import AudioSegment
import tempfile
import logging

# ==================== CONFIGURAÇÃO ====================
TOKEN = "8778081445:AAF8PEnPHntpnN3wjqNGAfTzWNPhJV_4VxM"  # COLE SEU TOKEN
ADMIN_ID = 5052937721  # COLE SEU ID

# Configurar logs
logging.basicConfig(level=logging.INFO)

# ==================== BANCO DE DADOS ====================
def init_db():
    conn = sqlite3.connect('dados.db')
    c = conn.cursor()
    
    # Gastos
    c.execute('''CREATE TABLE IF NOT EXISTS gastos
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  descricao TEXT,
                  valor REAL,
                  data TEXT)''')
    
    # Ganhos
    c.execute('''CREATE TABLE IF NOT EXISTS ganhos
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  descricao TEXT,
                  valor REAL,
                  data TEXT)''')
    
    # Dívidas (o que te devem)
    c.execute('''CREATE TABLE IF NOT EXISTS dividas
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  pessoa TEXT,
                  valor REAL,
                  motivo TEXT,
                  data TEXT)''')
    
    conn.commit()
    conn.close()

# ==================== FUNÇÕES ====================

def extrair_valor(texto):
    """Pega o valor numérico do texto"""
    numeros = re.findall(r'(\d+(?:[.,]\d+)?)', texto)
    if numeros:
        return float(numeros[0].replace(',', '.'))
    return None

def extrair_pessoa(texto):
    """Tenta encontrar um nome no texto"""
    # Lista de nomes comuns
    nomes = ['joão', 'joao', 'maria', 'jose', 'ana', 'carlos', 'pedro', 'paulo', 'lucas']
    
    texto_lower = texto.lower()
    for nome in nomes:
        if nome in texto_lower:
            return nome.capitalize()
    
    # Se encontrar palavra com maiúscula
    palavras = texto.split()
    for palavra in palavras:
        if palavra[0].isupper() and len(palavra) > 2:
            return palavra
    
    return None

# ==================== PROCESSAR TEXTO ====================

async def processar_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa qualquer texto que o usuário enviar"""
    user_id = update.effective_user.id
    texto = update.message.text
    texto_lower = texto.lower()
    
    logging.info(f"Mensagem de {user_id}: {texto}")
    
    conn = sqlite3.connect('dados.db')
    c = conn.cursor()
    
    # ===== GASTOS =====
    if any(p in texto_lower for p in ['gastei', 'gasto', 'paguei', 'comprei']):
        valor = extrair_valor(texto)
        if not valor:
            await update.message.reply_text("❓ Quanto você gastou? Ex: 'gastei 50 em pizza'")
            conn.close()
            return
        
        # Extrair descrição
        descricao = texto
        descricao = re.sub(r'gastei|gasto|paguei|comprei|em|r\$|reais', '', texto_lower)
        descricao = re.sub(str(valor).replace('.', ','), '', descricao)
        descricao = descricao.strip()
        
        if not descricao:
            descricao = "compra"
        
        # Salvar
        c.execute('''INSERT INTO gastos (user_id, descricao, valor, data)
                     VALUES (?, ?, ?, ?)''',
                  (user_id, descricao.capitalize(), valor, datetime.now()))
        conn.commit()
        
        await update.message.reply_text(f"💰 Gasto de R$ {valor:.2f} registrado: {descricao.capitalize()}")
        conn.close()
        return
    
    # ===== GANHOS =====
    if any(p in texto_lower for p in ['ganhei', 'recebi']):
        valor = extrair_valor(texto)
        if not valor:
            await update.message.reply_text("❓ Quanto você ganhou? Ex: 'ganhei 100 do joão'")
            conn.close()
            return
        
        descricao = texto
        descricao = re.sub(r'ganhei|recebi|r\$|reais', '', texto_lower)
        descricao = re.sub(str(valor).replace('.', ','), '', descricao)
        descricao = descricao.strip()
        
        if not descricao:
            descricao = "recebimento"
        
        c.execute('''INSERT INTO ganhos (user_id, descricao, valor, data)
                     VALUES (?, ?, ?, ?)''',
                  (user_id, descricao.capitalize(), valor, datetime.now()))
        conn.commit()
        
        await update.message.reply_text(f"💵 Ganho de R$ {valor:.2f} registrado: {descricao.capitalize()}")
        conn.close()
        return
    
    # ===== DÍVIDAS (devem pra você) =====
    if any(p in texto_lower for p in ['devendo', 'deve', 'divida']):
        valor = extrair_valor(texto)
        pessoa = extrair_pessoa(texto)
        
        if not pessoa:
            await update.message.reply_text("❓ Quem está devendo? Ex: 'joão ficou me devendo 50'")
            conn.close()
            return
        
        if not valor:
            await update.message.reply_text(f"❓ Quanto {pessoa} está devendo?")
            conn.close()
            return
        
        # Extrair motivo
        motivo = texto
        motivo = re.sub(r'devendo|deve|divida|para|me', '', texto_lower)
        motivo = re.sub(pessoa.lower(), '', motivo)
        motivo = re.sub(str(valor).replace('.', ','), '', motivo)
        motivo = motivo.strip()
        
        c.execute('''INSERT INTO dividas (user_id, pessoa, valor, motivo, data)
                     VALUES (?, ?, ?, ?, ?)''',
                  (user_id, pessoa, valor, motivo, datetime.now()))
        conn.commit()
        
        await update.message.reply_text(f"📝 Dívida registrada: {pessoa} deve R$ {valor:.2f} para você")
        conn.close()
        return
    
    # ===== CONSULTAR DÍVIDAS =====
    if 'quanto' in texto_lower and any(p in texto_lower for p in ['deve', 'devendo']):
        pessoa = extrair_pessoa(texto)
        
        if pessoa:
            c.execute('''SELECT SUM(valor) FROM dividas 
                         WHERE user_id = ? AND pessoa = ?''', (user_id, pessoa))
            total = c.fetchone()[0] or 0
            conn.close()
            
            if total > 0:
                await update.message.reply_text(f"💰 {pessoa} deve R$ {total:.2f} para você")
            else:
                await update.message.reply_text(f"✅ {pessoa} não tem dívidas com você")
        else:
            # Todas as dívidas
            c.execute('''SELECT pessoa, SUM(valor) FROM dividas 
                         WHERE user_id = ? GROUP BY pessoa''', (user_id,))
            dividas = c.fetchall()
            conn.close()
            
            if not dividas:
                await update.message.reply_text("✅ Ninguém está devendo para você")
                return
            
            resposta = "📊 *DÍVIDAS ATIVAS*\n\n"
            for pessoa, valor in dividas:
                resposta += f"👤 {pessoa}: R$ {valor:.2f}\n"
            
            await update.message.reply_text(resposta)
        return
    
    # ===== PAGAMENTO DE DÍVIDAS =====
    if any(p in texto_lower for p in ['pagou', 'quitou']):
        pessoa = extrair_pessoa(texto)
        valor = extrair_valor(texto)
        
        if not pessoa:
            await update.message.reply_text("❓ Quem pagou? Ex: 'joão pagou 30'")
            conn.close()
            return
        
        if not valor:
            await update.message.reply_text(f"❓ Quanto {pessoa} pagou?")
            conn.close()
            return
        
        # Buscar dívidas dessa pessoa
        c.execute('''SELECT id, valor FROM dividas 
                     WHERE user_id = ? AND pessoa = ?''', (user_id, pessoa))
        dividas = c.fetchall()
        
        if not dividas:
            await update.message.reply_text(f"✅ {pessoa} não tinha dívidas com você")
            conn.close()
            return
        
        valor_pago = valor
        valor_restante = valor_pago
        
        for divida_id, valor_divida in dividas:
            if valor_restante <= 0:
                break
            
            if valor_restante >= valor_divida:
                c.execute('DELETE FROM dividas WHERE id = ?', (divida_id,))
                valor_restante -= valor_divida
            else:
                novo_valor = valor_divida - valor_restante
                c.execute('UPDATE dividas SET valor = ? WHERE id = ?', (novo_valor, divida_id))
                valor_restante = 0
        
        conn.commit()
        
        # Verificar se ainda deve
        c.execute('''SELECT SUM(valor) FROM dividas WHERE user_id = ? AND pessoa = ?''', (user_id, pessoa))
        ainda_deve = c.fetchone()[0] or 0
        conn.close()
        
        if ainda_deve == 0:
            await update.message.reply_text(f"✅ {pessoa} quitou todas as dívidas!")
        else:
            await update.message.reply_text(f"💰 {pessoa} pagou R$ {valor_pago:.2f}. Ainda deve R$ {ainda_deve:.2f}")
        return
    
    # ===== CONSULTAR GASTOS =====
    if any(p in texto_lower for p in ['gastos', 'gastei']):
        if 'hoje' in texto_lower:
            hoje = datetime.now().strftime("%Y-%m-%d")
            c.execute('''SELECT descricao, valor FROM gastos 
                         WHERE user_id = ? AND date(data) = ?''', (user_id, hoje))
            gastos = c.fetchall()
            
            if not gastos:
                await update.message.reply_text("💰 Nenhum gasto hoje")
            else:
                total = sum(g[1] for g in gastos)
                resposta = f"📋 *GASTOS DE HOJE* - Total: R$ {total:.2f}\n\n"
                for desc, val in gastos:
                    resposta += f"• {desc}: R$ {val:.2f}\n"
                await update.message.reply_text(resposta)
            conn.close()
            return
    
    # ===== RESUMO GERAL =====
    if any(p in texto_lower for p in ['resumo', 'saldo', 'tudo']):
        # Gastos totais
        c.execute('''SELECT SUM(valor) FROM gastos WHERE user_id = ?''', (user_id,))
        total_gastos = c.fetchone()[0] or 0
        
        # Ganhos totais
        c.execute('''SELECT SUM(valor) FROM ganhos WHERE user_id = ?''', (user_id,))
        total_ganhos = c.fetchone()[0] or 0
        
        # Dívidas a receber
        c.execute('''SELECT SUM(valor) FROM dividas WHERE user_id = ?''', (user_id,))
        total_dividas = c.fetchone()[0] or 0
        
        conn.close()
        
        saldo = total_ganhos + total_dividas - total_gastos
        
        resposta = f"📊 *RESUMO COMPLETO*\n\n"
        resposta += f"💰 Gastos totais: R$ {total_gastos:.2f}\n"
        resposta += f"💵 Ganhos totais: R$ {total_ganhos:.2f}\n"
        resposta += f"📝 Dívidas a receber: R$ {total_dividas:.2f}\n"
        resposta += f"───────────────\n"
        resposta += f"💸 Saldo total: R$ {saldo:.2f}"
        
        await update.message.reply_text(resposta)
        return
    
    # ===== SE NÃO ENTENDEU =====
    await update.message.reply_text(
        "❓ Não entendi. Tente assim:\n\n"
        "💰 Gastar: 'gastei 50 em pizza'\n"
        "💵 Ganhar: 'ganhei 100 do joão'\n"
        "📝 Dívida: 'joão ficou me devendo 30'\n"
        "📊 Consultar: 'quanto joão me deve?'\n"
        "✅ Pagar: 'joão pagou 20'\n"
        "📋 Listar: 'meus gastos hoje'"
    )

# ==================== PROCESSAR ÁUDIO ====================

async def processar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Converte áudio em texto e processa"""
    user_id = update.effective_user.id
    
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
        
        # Mostrar o que entendeu
        await update.message.reply_text(f"📝 Você disse: {texto}")
        
        # Processar o texto
        update.message.text = texto
        await processar_texto(update, context)
        
    except Exception as e:
        await update.message.reply_text("❌ Não entendi o áudio. Fale mais claro ou use texto.")
        logging.error(f"Erro no áudio: {e}")

# ==================== COMANDOS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensagem inicial"""
    await update.message.reply_text(
        "👋 *Olá! Sou seu assistente pessoal!*\n\n"
        "Fale comigo naturalmente:\n\n"
        "💰 *Gastos:* 'gastei 50 em pizza'\n"
        "💵 *Ganhos:* 'ganhei 100 do joão'\n"
        "📝 *Dívidas:* 'joão ficou me devendo 30'\n"
        "📊 *Consultar:* 'quanto joão me deve?'\n"
        "✅ *Pagar:* 'joão pagou 20'\n"
        "📋 *Listar:* 'meus gastos hoje'\n"
        "📈 *Resumo:* 'meu saldo'\n\n"
        "🎤 *Envie áudios também!*",
        parse_mode='Markdown'
    )

# ==================== MAIN ====================

def main():
    # Iniciar banco
    init_db()
    
    # Criar bot
    app = Application.builder().token(TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_texto))
    app.add_handler(MessageHandler(filters.VOICE, processar_audio))
    
    print("="*50)
    print("🤖 BOT INICIADO COM SUCESSO!")
    print("="*50)
    
    app.run_polling()

if __name__ == "__main__":
    main()
