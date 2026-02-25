import os
import re
import sqlite3
import threading
import tempfile
from datetime import datetime, timedelta

# Importações do Flask para o servidor web
from flask import Flask

# Importações do Telegram Bot
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

# Importações para processamento de áudio
import speech_recognition as sr
from pydub import AudioSegment

# Carregar variáveis de ambiente
from dotenv import load_dotenv
load_dotenv()

# Configurações
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CONTATO = os.getenv("CONTATO")

# ==================== BANCO DE DADOS ====================

DB_NAME = 'assistente.db'

def init_db():
    """Inicializa o banco de dados com todas as tabelas"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Tabela de usuários para controle de acesso
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios (
                 telegram_id INTEGER PRIMARY KEY,
                 nome TEXT,
                 data_expiracao TEXT,
                 ativo INTEGER DEFAULT 1
    )''')
    
    # Tabela de códigos de acesso
    c.execute('''CREATE TABLE IF NOT EXISTS codigos (
                 codigo TEXT PRIMARY KEY,
                 dias INTEGER,
                 usado INTEGER DEFAULT 0
    )''')
    
    # Tabela de dívidas a receber (alguém te deve)
    c.execute('''CREATE TABLE IF NOT EXISTS dividas_receber (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 cliente TEXT,
                 valor REAL,
                 motivo TEXT,
                 data_criacao TEXT,
                 status TEXT DEFAULT 'pendente'
    )''')
    
    # Tabela de dívidas a pagar (você deve a alguém)
    c.execute('''CREATE TABLE IF NOT EXISTS dividas_pagar (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 credor TEXT,
                 valor REAL,
                 motivo TEXT,
                 data_criacao TEXT,
                 status TEXT DEFAULT 'pendente'
    )''')
    
    # Tabela de gastos
    c.execute('''CREATE TABLE IF NOT EXISTS gastos (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 descricao TEXT,
                 valor REAL,
                 data TEXT,
                 categoria TEXT
    )''')
    
    # Tabela de ganhos
    c.execute('''CREATE TABLE IF NOT EXISTS ganhos (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 descricao TEXT,
                 valor REAL,
                 data TEXT,
                 de_quem TEXT
    )''')
    
    # Tabela de vendas
    c.execute('''CREATE TABLE IF NOT EXISTS vendas (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 cliente TEXT,
                 produto TEXT,
                 valor REAL,
                 data TEXT,
                 pago INTEGER DEFAULT 1
    )''')
    
    # Tabela de produtos (para facilitar vendas)
    c.execute('''CREATE TABLE IF NOT EXISTS produtos (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 nome TEXT,
                 preco REAL
    )''')
    
    # Tabela de pagamentos (para registrar quitações de dívidas)
    c.execute('''CREATE TABLE IF NOT EXISTS pagamentos (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 divida_id INTEGER,
                 tipo TEXT, -- 'pagar' ou 'receber'
                 valor REAL,
                 data TEXT
    )''')
    
    # Tabela de clientes
    c.execute('''CREATE TABLE IF NOT EXISTS clientes (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 nome TEXT,
                 telefone TEXT
    )''')
    
    # Tabela de memória (para o comando "lembra")
    c.execute('''CREATE TABLE IF NOT EXISTS memoria (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 chave TEXT,
                 valor TEXT,
                 data TEXT
    )''')
    
    conn.commit()
    conn.close()

# ==================== FUNÇÕES AUXILIARES ====================

def verificar_acesso(user_id: int) -> bool:
    """Verifica se o usuário tem acesso ao bot"""
    if user_id == ADMIN_ID:
        return True
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''SELECT data_expiracao FROM usuarios WHERE telegram_id = ? AND ativo = 1''', (user_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return False
    
    expiracao = result[0]
    if expiracao is None:  # Acesso vitalício
        return True
    
    try:
        data_exp = datetime.strptime(expiracao, "%Y-%m-%d")
        if data_exp >= datetime.now():
            return True
    except ValueError:
        pass # Se a data for inválida, nega acesso
    
    return False

def extrair_valor(texto: str) -> float:
    """Extrai um valor numérico de um texto"""
    # Padrões para encontrar valores como "50", "50.50", "50,50", "R$ 50"
    padrao = r'(?:R\$?\s*)?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+,\d{2}|\d+)'
    match = re.search(padrao, texto.replace('.', '').replace(',', '.'), re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None

def extrair_pessoa(texto: str) -> str:
    """Tenta extrair um nome próprio (pessoa) do texto"""
    # Simplificação: pega a primeira palavra com letra maiúscula que não está no início
    palavras = texto.split()
    for i, palavra in enumerate(palavras[1:], 1): # Ignora a primeira palavra
        if palavra[0].isupper() and len(palavra) > 2:
            return palavra
    return None

def extrair_data(texto: str) -> str:
    """Extrai uma data ou retorna a data atual com base em palavras-chave"""
    texto_lower = texto.lower()
    hoje = datetime.now()
    
    if 'hoje' in texto_lower:
        return hoje.strftime("%Y-%m-%d")
    elif 'ontem' in texto_lower:
        return (hoje - timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Tenta encontrar uma data no formato DD/MM ou YYYY-MM-DD
    padrao_data = r'(\d{4}-\d{2}-\d{2}|\d{2}/\d{2})'
    match = re.search(padrao_data, texto)
    if match:
        data_str = match.group(1)
        if '-' in data_str: # Formato YYYY-MM-DD
            return data_str
        else: # Formato DD/MM
            try:
                dia, mes = map(int, data_str.split('/'))
                return hoje.replace(day=dia, month=mes).strftime("%Y-%m-%d")
            except ValueError:
                pass
    
    return hoje.strftime("%Y-%m-%d")

def gerar_codigo() -> str:
    """Gera um código de acesso aleatório"""
    import random
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

# ==================== INTELIGÊNCIA DO ASSISTENTE ====================

class AssistenteInteligente:
    def __init__(self, user_id):
        self.user_id = user_id
        self.conn = sqlite3.connect(DB_NAME)
        self.c = self.conn.cursor()
    
    def __del__(self):
        self.conn.close()
    
    def processar(self, texto: str) -> str:
        """Processa o texto e retorna uma resposta"""
        texto_lower = texto.lower()
        
        # ===== CUMPRIMENTOS =====
        if any(p in texto_lower for p in ['oi', 'olá', 'ola', 'boa tarde', 'bom dia', 'boa noite']):
            return self.cumprimentar()
        
        # ===== DÍVIDAS =====
        if any(p in texto_lower for p in ['deve', 'devendo', 'dívida', 'divida']):
            return self.processar_divida(texto)
        
        # ===== GASTOS =====
        if any(p in texto_lower for p in ['gastei', 'gasto', 'paguei', 'comprei']):
            return self.registrar_gasto(texto)
        
        # ===== GANHOS =====
        if any(p in texto_lower for p in ['ganhei', 'recebi']):
            return self.registrar_ganho(texto)
        
        # ===== VENDAS =====
        if any(p in texto_lower for p in ['vendi', 'venda']):
            return self.registrar_venda(texto)
        
        # ===== PERGUNTAS =====
        if any(p in texto_lower for p in ['quanto', 'total', 'saldo', 'resumo', 'extrato']):
            return self.responder_pergunta(texto)
        
        # ===== CLIENTES =====
        if any(p in texto_lower for p in ['cliente', 'clientes']):
            return self.gerenciar_clientes(texto)
        
        # ===== MEMÓRIA =====
        if 'lembra' in texto_lower:
            return self.lembrar(texto)
        
        # Se não entendeu
        return self.nao_entendi()
    
    def cumprimentar(self) -> str:
        hora = datetime.now().hour
        if hora < 12:
            periodo = "Bom dia"
        elif hora < 18:
            periodo = "Boa tarde"
        else:
            periodo = "Boa noite"
        
        return f"{periodo}! Como posso ajudar? Posso registrar gastos, ganhos, dívidas, vendas, ou responder perguntas sobre suas finanças."
    
    def processar_divida(self, texto: str) -> str:
        """Processa tudo relacionado a dívidas"""
        texto_lower = texto.lower()
        pessoa = extrair_pessoa(texto)
        valor = extrair_valor(texto)
        
        # ===== REGISTRAR DÍVIDA (alguém deve para você) =====
        if any(p in texto_lower for p in ['ficou devendo', 'está devendo', 'me deve']):
            if pessoa and valor:
                motivo = texto
                for p in ['ficou', 'devendo', 'está', 'me', 'deve', str(valor).replace('.', ','), 'reais', 'r$']:
                    motivo = motivo.lower().replace(p, '')
                motivo = motivo.strip()
                
                self.c.execute('''INSERT INTO dividas_receber 
                                 (user_id, cliente, valor, motivo, data_criacao, status)
                                 VALUES (?, ?, ?, ?, ?, ?)''',
                              (self.user_id, pessoa, valor, motivo, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'pendente'))
                self.conn.commit()
                
                return f"✅ Entendi! {pessoa} ficou devendo R$ {valor:.2f} para você. {motivo if motivo else ''}"
        
        # ===== REGISTRAR DÍVIDA (você deve para alguém) =====
        elif any(p in texto_lower for p in ['devo para', 'devo ao', 'devo a']):
            if pessoa and valor:
                motivo = texto
                for p in ['devo', 'para', 'ao', 'a', str(valor).replace('.', ','), 'reais', 'r$']:
                    motivo = motivo.lower().replace(p, '')
                motivo = motivo.strip()
                
                self.c.execute('''INSERT INTO dividas_pagar 
                                 (user_id, credor, valor, motivo, data_criacao, status)
                                 VALUES (?, ?, ?, ?, ?, ?)''',
                              (self.user_id, pessoa, valor, motivo, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'pendente'))
                self.conn.commit()
                
                return f"✅ Entendi! Você deve R$ {valor:.2f} para {pessoa}. {motivo if motivo else ''}"
        
        # ===== PAGAMENTO (alguém pagou você) =====
        elif any(p in texto_lower for p in ['pagou', 'quitou', 'acertou']):
            if pessoa:
                if valor:
                    # Buscar dívidas ativas dessa pessoa
                    self.c.execute('''SELECT id, valor FROM dividas_receber 
                                     WHERE user_id = ? AND cliente = ? AND status = 'pendente'
                                     ORDER BY data_criacao''', (self.user_id, pessoa))
                    dividas = self.c.fetchall()
                    
                    if not dividas:
                        return f"Não encontrei nenhuma dívida pendente de {pessoa}."
                    
                    valor_pago = valor
                    restante = valor_pago
                    
                    for divida_id, valor_divida in dividas:
                        if restante <= 0:
                            break
                        
                        if restante >= valor_divida:
                            self.c.execute('''UPDATE dividas_receber SET status = 'pago' WHERE id = ?''', (divida_id,))
                            self.c.execute('''INSERT INTO pagamentos (divida_id, tipo, valor, data)
                                             VALUES (?, ?, ?, ?)''', (divida_id, 'receber', valor_divida, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                            restante -= valor_divida
                        else:
                            novo_valor = valor_divida - restante
                            self.c.execute('''UPDATE dividas_receber SET valor = ? WHERE id = ?''', (novo_valor, divida_id))
                            self.c.execute('''INSERT INTO pagamentos (divida_id, tipo, valor, data)
                                             VALUES (?, ?, ?, ?)''', (divida_id, 'receber', restante, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                            restante = 0
                    
                    self.conn.commit()
                    
                    # Verificar saldo restante
                    self.c.execute('''SELECT SUM(valor) FROM dividas_receber 
                                     WHERE user_id = ? AND cliente = ? AND status = 'pendente' ''', 
                                  (self.user_id, pessoa))
                    saldo = self.c.fetchone()[0] or 0
                    
                    if saldo == 0:
                        return f"✅ Ótimo! {pessoa} quitou todas as dívidas com você!"
                    else:
                        return f"✅ Recebido R$ {valor_pago:.2f} de {pessoa}. Ainda resta R$ {saldo:.2f}."
                else:
                    return f"Quanto {pessoa} pagou?"
        
        # ===== PAGAMENTO (você pagou alguém) =====
        elif any(p in texto_lower for p in ['paguei para', 'paguei ao']):
            if pessoa:
                if valor:
                    self.c.execute('''SELECT id, valor FROM dividas_pagar 
                                     WHERE user_id = ? AND credor = ? AND status = 'pendente'
                                     ORDER BY data_criacao''', (self.user_id, pessoa))
                    dividas = self.c.fetchall()
                    
                    if not dividas:
                        return f"Você não tem dívidas pendentes com {pessoa}."
                    
                    valor_pago = valor
                    restante = valor_pago
                    
                    for divida_id, valor_divida in dividas:
                        if restante <= 0:
                            break
                        
                        if restante >= valor_divida:
                            self.c.execute('''UPDATE dividas_pagar SET status = 'pago' WHERE id = ?''', (divida_id,))
                            self.c.execute('''INSERT INTO pagamentos (divida_id, tipo, valor, data)
                                             VALUES (?, ?, ?, ?)''', (divida_id, 'pagar', valor_divida, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                            restante -= valor_divida
                        else:
                            novo_valor = valor_divida - restante
                            self.c.execute('''UPDATE dividas_pagar SET valor = ? WHERE id = ?''', (novo_valor, divida_id))
                            self.c.execute('''INSERT INTO pagamentos (divida_id, tipo, valor, data)
                                             VALUES (?, ?, ?, ?)''', (divida_id, 'pagar', restante, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                            restante = 0
                    
                    self.conn.commit()
                    
                    self.c.execute('''SELECT SUM(valor) FROM dividas_pagar 
                                     WHERE user_id = ? AND credor = ? AND status = 'pendente' ''', 
                                  (self.user_id, pessoa))
                    saldo = self.c.fetchone()[0] or 0
                    
                    if saldo == 0:
                        return f"✅ Você quitou todas as dívidas com {pessoa}!"
                    else:
                        return f"✅ Pago R$ {valor_pago:.2f} para {pessoa}. Ainda deve R$ {saldo:.2f}."
                else:
                    return f"Quanto você pagou para {pessoa}?"
        
        # ===== CONSULTAR DÍVIDAS =====
        elif any(p in texto_lower for p in ['quanto', 'saldo', 'devo', 'deve']):
            if pessoa:
                # Verificar se a pessoa deve para você
                self.c.execute('''SELECT SUM(valor) FROM dividas_receber 
                                 WHERE user_id = ? AND cliente = ? AND status = 'pendente' ''', 
                              (self.user_id, pessoa))
                a_receber = self.c.fetchone()[0] or 0
                
                # Verificar se você deve para a pessoa
                self.c.execute('''SELECT SUM(valor) FROM dividas_pagar 
                                 WHERE user_id = ? AND credor = ? AND status = 'pendente' ''', 
                              (self.user_id, pessoa))
                a_pagar = self.c.fetchone()[0] or 0
                
                if a_receber > 0 and a_pagar > 0:
                    return f"Sobre {pessoa}: você tem R$ {a_receber:.2f} a receber e R$ {a_pagar:.2f} a pagar. Saldo líquido: R$ {a_receber - a_pagar:.2f}"
                elif a_receber > 0:
                    return f"{pessoa} te deve R$ {a_receber:.2f}"
                elif a_pagar > 0:
                    return f"Você deve R$ {a_pagar:.2f} para {pessoa}"
                else:
                    return f"Não há dívidas com {pessoa}."
            else:
                # Todas as dívidas
                self.c.execute('''SELECT cliente, SUM(valor) FROM dividas_receber 
                                 WHERE user_id = ? AND status = 'pendente' GROUP BY cliente''', (self.user_id,))
                a_receber = self.c.fetchall()
                
                self.c.execute('''SELECT credor, SUM(valor) FROM dividas_pagar 
                                 WHERE user_id = ? AND status = 'pendente' GROUP BY credor''', (self.user_id,))
                a_pagar = self.c.fetchall()
                
                resposta = "📊 *RESUMO DE DÍVIDAS*\n\n"
                
                if a_receber:
                    resposta += "💰 *A RECEBER:*\n"
                    for cliente, valor in a_receber:
                        resposta += f"  👤 {cliente}: R$ {valor:.2f}\n"
                    resposta += "\n"
                
                if a_pagar:
                    resposta += "💸 *A PAGAR:*\n"
                    for credor, valor in a_pagar:
                        resposta += f"  👤 {credor}: R$ {valor:.2f}\n"
                    resposta += "\n"
                
                if not a_receber and not a_pagar:
                    resposta = "🎉 Parabéns! Você não tem nenhuma dívida pendente!"
                
                return resposta
        
        return None
    
    def registrar_gasto(self, texto: str) -> str:
        """Registra um gasto"""
        valor = extrair_valor(texto)
        if not valor:
            return "Quanto você gastou?"
        
        # Extrair descrição
        descricao = texto
        palavras_remover = ['gastei', 'gasto', 'paguei', 'comprei', 'em', str(valor).replace('.', ','), 'reais', 'r$']
        for palavra in palavras_remover:
            descricao = descricao.lower().replace(palavra, '')
        descricao = descricao.strip()
        
        if not descricao:
            descricao = "compra"
        
        # Determinar categoria
        categoria = "outros"
        if any(p in descricao for p in ['pizza', 'lanche', 'comida', 'restaurante', 'almoço', 'jantar']):
            categoria = "alimentação"
        elif any(p in descricao for p in ['uber', 'taxi', 'ônibus', 'onibus', 'metro', 'combustível']):
            categoria = "transporte"
        elif any(p in descricao for p in ['mercado', 'supermercado']):
            categoria = "mercado"
        
        self.c.execute('''INSERT INTO gastos (user_id, descricao, valor, data, categoria)
                         VALUES (?, ?, ?, ?, ?)''',
                      (self.user_id, descricao.capitalize(), valor, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), categoria))
        self.conn.commit()
        
        return f"💰 Gasto registrado: R$ {valor:.2f} em {descricao.capitalize()}"
    
    def registrar_ganho(self, texto: str) -> str:
        """Registra um ganho"""
        valor = extrair_valor(texto)
        if not valor:
            return "Quanto você ganhou?"
        
        # Extrair descrição e de quem
        descricao = texto
        de_quem = extrair_pessoa(texto)
        
        palavras_remover = ['ganhei', 'recebi', str(valor).replace('.', ','), 'reais', 'r$']
        for palavra in palavras_remover:
            descricao = descricao.lower().replace(palavra, '')
        descricao = descricao.strip()
        
        if not descricao:
            descricao = "recebimento"
        
        self.c.execute('''INSERT INTO ganhos (user_id, descricao, valor, data, de_quem)
                         VALUES (?, ?, ?, ?, ?)''',
                      (self.user_id, descricao.capitalize(), valor, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), de_quem))
        self.conn.commit()
        
        if de_quem:
            return f"💵 Recebido R$ {valor:.2f} de {de_quem} ({descricao.capitalize()})"
        else:
            return f"💵 Ganho registrado: R$ {valor:.2f} - {descricao.capitalize()}"
    
    def registrar_venda(self, texto: str) -> str:
        """Registra uma venda"""
        valor = extrair_valor(texto)
        cliente = extrair_pessoa(texto)
        
        if not cliente:
            return "Para quem foi a venda?"
        
        # Procurar produto
        self.c.execute('''SELECT nome, preco FROM produtos WHERE user_id = ?''', (self.user_id,))
        produtos = self.c.fetchall()
        
        produto_encontrado = None
        for nome, preco in produtos:
            if nome.lower() in texto.lower():
                produto_encontrado = (nome, preco)
                break
        
        if produto_encontrado and not valor:
            valor = produto_encontrado[1]
        
        if not valor:
            return "Qual o valor da venda?"
        
        # Verificar se é fiado
        fiado = any(p in texto.lower() for p in ['fiado', 'pra pagar', 'depois', 'confia'])
        
        if fiado:
            self.c.execute('''INSERT INTO dividas_receber 
                             (user_id, cliente, valor, motivo, data_criacao, status)
                             VALUES (?, ?, ?, ?, ?, ?)''',
                          (self.user_id, cliente, valor, f"Venda fiado", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'pendente'))
            self.conn.commit()
            return f"📝 Venda fiado para {cliente}: R$ {valor:.2f}. Registrei como dívida."
        else:
            self.c.execute('''INSERT INTO vendas (user_id, cliente, produto, valor, data, pago)
                             VALUES (?, ?, ?, ?, ?, ?)''',
                          (self.user_id, cliente, produto_encontrado[0] if produto_encontrado else "produto", 
                           valor, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 1))
            self.conn.commit()
            return f"✅ Venda para {cliente}: R$ {valor:.2f}. Recebido à vista!"
    
    def responder_pergunta(self, texto: str) -> str:
        """Responde perguntas sobre finanças"""
        texto_lower = texto.lower()
        
        # ===== SALDO =====
        if 'saldo' in texto_lower:
            # Gastos
            self.c.execute('''SELECT SUM(valor) FROM gastos WHERE user_id = ?''', (self.user_id,))
            total_gastos = self.c.fetchone()[0] or 0
            
            # Ganhos
            self.c.execute('''SELECT SUM(valor) FROM ganhos WHERE user_id = ?''', (self.user_id,))
            total_ganhos = self.c.fetchone()[0] or 0
            
            # Vendas
            self.c.execute('''SELECT SUM(valor) FROM vendas WHERE user_id = ?''', (self.user_id,))
            total_vendas = self.c.fetchone()[0] or 0
            
            # Dívidas a receber
            self.c.execute('''SELECT SUM(valor) FROM dividas_receber WHERE user_id = ? AND status = 'pendente' ''', (self.user_id,))
            a_receber = self.c.fetchone()[0] or 0
            
            # Dívidas a pagar
            self.c.execute('''SELECT SUM(valor) FROM dividas_pagar WHERE user_id = ? AND status = 'pendente' ''', (self.user_id,))
            a_pagar = self.c.fetchone()[0] or 0
            
            saldo_total = (total_ganhos + total_vendas + a_receber) - (total_gastos + a_pagar)
            
            return f"💰 *SEU SALDO TOTAL*\n\n" \
                   f"Ganhos: R$ {total_ganhos:.2f}\n" \
                   f"Vendas: R$ {total_vendas:.2f}\n" \
                   f"A receber: R$ {a_receber:.2f}\n" \
                   f"Gastos: R$ {total_gastos:.2f}\n" \
                   f"A pagar: R$ {a_pagar:.2f}\n" \
                   f"═══════════════\n" \
                   f"Saldo líquido: R$ {saldo_total:.2f}"
        
        # ===== GASTOS =====
        elif any(p in texto_lower for p in ['gastei', 'gastos', 'gasto']):
            if 'hoje' in texto_lower:
                data = datetime.now().strftime("%Y-%m-%d")
                self.c.execute('''SELECT descricao, valor FROM gastos 
                                 WHERE user_id = ? AND date(data) = ? ORDER BY data DESC''', (self.user_id, data))
            elif 'ontem' in texto_lower:
                data = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                self.c.execute('''SELECT descricao, valor FROM gastos 
                                 WHERE user_id = ? AND date(data) = ? ORDER BY data DESC''', (self.user_id, data))
            else:
                self.c.execute('''SELECT descricao, valor, date(data) FROM gastos 
                                 WHERE user_id = ? ORDER BY data DESC LIMIT 10''', (self.user_id,))
            
            gastos = self.c.fetchall()
            
            if not gastos:
                return "Nenhum gasto encontrado."
            
            if len(gastos[0]) == 3:  # Lista geral
                resposta = "📝 *ÚLTIMOS GASTOS*\n\n"
                for desc, val, data in gastos:
                    resposta += f"📅 {data}: {desc} - R$ {val:.2f}\n"
            else:  # Período específico
                total = sum(g[1] for g in gastos)
                resposta = f"💰 *GASTOS DO DIA*\n\nTotal: R$ {total:.2f}\n\n"
                for desc, val in gastos:
                    resposta += f"• {desc}: R$ {val:.2f}\n"
            
            return resposta
        
        # ===== GANHOS =====
        elif any(p in texto_lower for p in ['ganhei', 'ganhos', 'recebi']):
            self.c.execute('''SELECT descricao, valor, date(data) FROM ganhos 
                             WHERE user_id = ? ORDER BY data DESC LIMIT 10''', (self.user_id,))
            ganhos = self.c.fetchall()
            
            if not ganhos:
                return "Nenhum ganho registrado."
            
            resposta = "💵 *ÚLTIMOS GANHOS*\n\n"
            for desc, val, data in ganhos:
                resposta += f"📅 {data}: {desc} - R$ {val:.2f}\n"
            
            return resposta
        
        # ===== RESUMO =====
        elif 'resumo' in texto_lower or 'extrato' in texto_lower:
            data = extrair_data(texto)
            
            # Gastos
            self.c.execute('''SELECT SUM(valor) FROM gastos WHERE user_id = ? AND date(data) = ?''', (self.user_id, data))
            gastos = self.c.fetchone()[0] or 0
            
            # Ganhos
            self.c.execute('''SELECT SUM(valor) FROM ganhos WHERE user_id = ? AND date(data) = ?''', (self.user_id, data))
            ganhos = self.c.fetchone()[0] or 0
            
            # Vendas
            self.c.execute('''SELECT SUM(valor) FROM vendas WHERE user_id = ? AND date(data) = ?''', (self.user_id, data))
            vendas = self.c.fetchone()[0] or 0
            
            # Pagamentos recebidos
            self.c.execute('''SELECT SUM(p.valor) FROM pagamentos p
                             JOIN dividas_receber d ON p.divida_id = d.id
                             WHERE d.user_id = ? AND date(p.data) = ? AND p.tipo = 'receber' ''', (self.user_id, data))
            pagos = self.c.fetchone()[0] or 0
            
            saldo = ganhos + vendas + pagos - gastos
            
            return f"📊 *RESUMO DO DIA {data}*\n\n" \
                   f"💰 Gastos: R$ {gastos:.2f}\n" \
                   f"💵 Ganhos: R$ {ganhos:.2f}\n" \
                   f"🛒 Vendas: R$ {vendas:.2f}\n" \
                   f"💳 Pagamentos: R$ {pagos:.2f}\n" \
                   f"═══════════════\n" \
                   f"💸 Saldo do dia: R$ {saldo:.2f}"
        
        return "Não entendi sua pergunta. Pode reformular?"
    
    def gerenciar_clientes(self, texto: str) -> str:
        """Gerencia clientes"""
        texto_lower = texto.lower()
        
        if 'novo cliente' in texto_lower:
            return "Para cadastrar um cliente, me diga o nome e telefone. Ex: 'cadastrar cliente João 99999-9999'"
        
        if 'listar clientes' in texto_lower or 'meus clientes' in texto_lower:
            self.c.execute('''SELECT nome, telefone FROM clientes WHERE user_id = ?''', (self.user_id,))
            clientes = self.c.fetchall()
            
            if not clientes:
                return "Você ainda não tem clientes cadastrados."
            
            resposta = "📋 *SEUS CLIENTES*\n\n"
            for nome, tel in clientes:
                resposta += f"👤 {nome}\n"
                if tel:
                    resposta += f"📞 {tel}\n"
                resposta += "─" * 20 + "\n"
            
            return resposta
        
        return None
    
    def lembrar(self, texto: str) -> str:
        """Guarda informações para lembrar depois"""
        texto_lower = texto.lower()
        
        if 'que' in texto_lower:
            # Extrair o que ele quer lembrar
            partes = texto_lower.split('que', 1)
            if len(partes) > 1:
                info = partes[1].strip()
                chave = f"memoria_{datetime.now().timestamp()}"
                self.c.execute('''INSERT INTO memoria (user_id, chave, valor, data)
                                 VALUES (?, ?, ?, ?)''',
                              (self.user_id, chave, info, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                self.conn.commit()
                return f"✅ Ok, vou lembrar: {info}"
        
        return "O que você quer que eu lembre?"
    
    def nao_entendi(self) -> str:
        return "Desculpe, não entendi. Você pode falar de outra forma? Por exemplo:\n" \
               "• 'João ficou me devendo 50 reais'\n" \
               "• 'Quanto João me deve?'\n" \
               "• 'Gastei 30 reais em pizza'\n" \
               "• 'Meu saldo total'\n" \
               "• 'Resumo de hoje'"

# ==================== HANDLERS DO TELEGRAM ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensagem inicial"""
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        await update.message.reply_text(
            "👑 *ASSISTENTE PESSOAL - ADMIN*\n\n"
            "Sou seu assistente inteligente! Entendo conversa natural.\n\n"
            "💬 *Fale comigo como se fosse uma pessoa:*\n"
            "• 'João ficou me devendo 50 reais do lanche'\n"
            "• 'Quanto João me deve?'\n"
            "• 'João pagou 30 reais'\n"
            "• 'Gastei 50 reais em pizza'\n"
            "• 'Ganhei 100 reais do Paulo'\n"
            "• 'Meu saldo total'\n"
            "• 'Resumo de hoje'\n\n"
            "🎤 *Envie áudios também!*\n\n"
            "/codigos - Para gerar códigos de acesso",
            parse_mode='Markdown'
        )
    elif verificar_acesso(user_id):
        await update.message.reply_text(
            "👋 *Olá! Sou seu assistente pessoal*\n\n"
            "Pode falar comigo naturalmente, como se fosse uma pessoa:\n\n"
            "💰 *Dívidas:* 'João ficou me devendo 50 reais'\n"
            "📊 *Consultar:* 'Quanto João me deve?'\n"
            "💳 *Pagar:* 'João pagou 30 reais'\n"
            "💵 *Gastos:* 'Gastei 50 em pizza'\n"
            "💸 *Ganhos:* 'Ganhei 100 do Paulo'\n"
            "📈 *Resumo:* 'Meu saldo' ou 'Resumo de hoje'\n\n"
            "🎤 *E também entendo áudios!*",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"👋 *Assistente Pessoal*\n\n"
            f"Para usar, você precisa de um código de acesso.\n"
            f"Use: /usar [CÓDIGO]\n\n"
            f"📞 Contato: {CONTATO}",
            parse_mode='Markdown'
        )

async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa mensagens de texto"""
    user_id = update.effective_user.id
    
    if not verificar_acesso(user_id):
        await update.message.reply_text(
            f"❌ Acesso negado! Contato: {CONTATO}",
            parse_mode='Markdown'
        )
        return
    
    texto = update.message.text
    
    # Mostrar que está processando
    await update.message.reply_chat_action("typing")
    
    # Processar com o assistente
    assistente = AssistenteInteligente(user_id)
    resposta = assistente.processar(texto)
    
    await update.message.reply_text(resposta, parse_mode='Markdown')

async def processar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa áudio"""
    user_id = update.effective_user.id
    
    if not verificar_acesso(user_id):
        return
    
    # Mostrar que está processando
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
        await update.message.reply_text(f"📝 *Você disse:* {texto}", parse_mode='Markdown')
        
        # Processar o texto
        assistente = AssistenteInteligente(user_id)
        resposta = assistente.processar(texto)
        
        await update.message.reply_text(resposta, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text("❌ Não consegui entender. Fale mais claramente ou use texto.")

async def codigos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gerar códigos (só admin)"""
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
        "🎫 *GERAR CÓDIGOS DE ACESSO*",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def usar_codigo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usar código de acesso"""
    if not context.args:
        await update.message.reply_text("Use: /usar [CÓDIGO]")
        return
    
    codigo = context.args[0].upper()
    user_id = update.effective_user.id
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''SELECT dias, usado FROM codigos WHERE codigo = ?''', (codigo,))
    result = c.fetchone()
    
    if not result:
        await update.message.reply_text("❌ Código inválido!")
        conn.close()
        return
    
    dias, usado = result
    
    if usado:
        await update.message.reply_text("❌ Código já foi usado!")
        conn.close()
        return
    
    expiracao = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d") if dias else None
    nome = update.effective_user.first_name or "Cliente"
    
    c.execute('''INSERT OR REPLACE INTO usuarios (telegram_id, nome, data_expiracao, ativo)
                 VALUES (?, ?, ?, 1)''', (user_id, nome, expiracao))
    
    c.execute('''UPDATE codigos SET usado = 1 WHERE codigo = ?''', (codigo,))
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text(
        f"🎉 *Acesso liberado!*\n\n"
        f"⏳ {dias if dias else 'Vitalício'} dias\n"
        f"✅ Agora pode falar comigo naturalmente!",
        parse_mode='Markdown'
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa botões"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    dias_map = {
        'codigo_7': 7,
        'codigo_15': 15,
        'codigo_30': 30,
        'codigo_vitalicio': None
    }
    
    dias = dias_map.get(query.data)
    codigo = gerar_codigo()
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''INSERT INTO codigos (codigo, dias) VALUES (?, ?)''', (codigo, dias))
    conn.commit()
    conn.close()
    
    tipo = "VITALÍCIO" if dias is None else f"{dias} DIAS"
    
    await query.edit_message_text(
        f"✅ *Código gerado!*\n\n"
        f"`{codigo}`\n"
        f"⏳ {tipo}\n\n"
        f"Cliente usa: /usar {codigo}",
        parse_mode='Markdown'
    )

# ==================== SERVIDOR WEB ====================
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "🤖 Assistente Pessoal Inteligente Rodando 24/7!"

def run_web():
    app_web.run(host='0.0.0.0', port=8080)

# ==================== MAIN ====================
def main():
    # Iniciar banco
    init_db()
    
    # Iniciar servidor web (para o Fly.io não dar timeout)
    threading.Thread(target=run_web, daemon=True).start()
    
    # Criar bot
    app = Application.builder().token(TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("usar", usar_codigo))
    app.add_handler(CommandHandler("codigos", codigos))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem))
    app.add_handler(MessageHandler(filters.VOICE, processar_audio))
    
    print("="*60)
    print("🤖 ASSISTENTE PESSOAL INTELIGENTE INICIADO!")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print("="*60)
    
    app.run_polling()

if __name__ == "__main__":
    main()
