import socket
import threading

HOST = '127.0.0.1'
PORT = 65432

# Dicionário para associar nomes de usuário aos sockets de conexão
clientes_conectados = {}

def gerenciar_cliente(conexao):
    try:
        # A primeira mensagem do cliente é o nome de usuário
        nome_usuario = conexao.recv(1024).decode('utf-8')
        if not nome_usuario:
            return

        print(f"Nova conexão: {nome_usuario} se juntou ao chat.")
        clientes_conectados[nome_usuario] = conexao

        # Loop para receber mensagens do cliente
        while True:
            # Recebe a mensagem do cliente
            mensagem = conexao.recv(1024).decode('utf-8')
            if not mensagem:
                break
            
            print(f"Mensagem de {nome_usuario}: {mensagem}")
            
            # Formato esperado: "destinatario:mensagem_aqui"
            if ':' in mensagem:
                destinatario, texto_mensagem = mensagem.split(':', 1)
                destinatario = destinatario.strip()
                texto_mensagem = texto_mensagem.strip()
                
                # Envia a mensagem apenas para o destinatário correto
                if destinatario in clientes_conectados:
                    socket_destinatario = clientes_conectados[destinatario]
                    socket_destinatario.sendall(f"[{nome_usuario}] diz: {texto_mensagem}".encode('utf-8'))
                else:
                    # Informa o remetente se o destinatário não for encontrado
                    conexao.sendall(b"Usuario nao encontrado.")
            else:
                conexao.sendall(b"Formato de mensagem invalido. Use: 'destinatario:sua_mensagem'")
    
    finally:
        # Quando a conexão é encerrada, remove o cliente
        if nome_usuario in clientes_conectados:
            print(f"{nome_usuario} desconectou.")
            del clientes_conectados[nome_usuario]
        conexao.close()

def iniciar_servidor():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, PORT))
        s.listen()
        print(f"Servidor ouvindo em {HOST}:{PORT}")
        
        while True:
            # Aguarda uma nova conexão
            conexao, endereco = s.accept()
            # Cria uma nova thread para gerenciar essa conexão
            thread_cliente = threading.Thread(target=gerenciar_cliente, args=(conexao,))
            thread_cliente.start()

iniciar_servidor()