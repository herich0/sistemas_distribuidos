import socket
import threading


HOST = '127.0.0.1'
PORT = 65432

# Função para receber mensagens do servidor
def receber_mensagens(s):
    while True:
        try:
            data = s.recv(1024)
            if not data:
                break
            print(f"\n{data.decode('utf-8')}")
        except:
            print("Desconectado do servidor.")
            break

# Função para enviar mensagens para o servidor
def enviar_mensagens(s):
    while True:
        mensagem = input()
        s.sendall(mensagem.encode('utf-8'))
        if mensagem.lower() == 'sair':
            break

# Conecta ao servidor e inicia as threads
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.connect((HOST, PORT))
    
    # Envia o nome de usuário para o servidor
    nome_usuario = input("Digite seu nome de usuário: ")
    s.sendall(nome_usuario.encode('utf-8'))
    
    print("Conectado! Para enviar uma mensagem privada, use o formato 'destinatario:sua_mensagem'.")
    
    # Cria threads para receber e enviar mensagens simultaneamente
    thread_receber = threading.Thread(target=receber_mensagens, args=(s,))
    thread_enviar = threading.Thread(target=enviar_mensagens, args=(s,))
    
    thread_receber.start()
    thread_enviar.start()

    thread_enviar.join()
    thread_receber.join()