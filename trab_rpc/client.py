import grpc
import threading
import time
import os # Para limpar o terminal

# Importa as classes geradas
import game_pb2
import game_pb2_grpc

# --- Variáveis de Estado Global do Cliente ---
# (Simples, apenas para este exemplo)
PLAYER_NAME = ""
CURRENT_ROOM_ID = ""
CURRENT_GAME_STATE = None
STATE_LOCK = threading.Lock() # Lock para proteger o CURRENT_GAME_STATE
IS_MY_TURN = False
GAME_OVER = False

def clear_screen():
    # Limpa o terminal para uma "UI" melhor
    os.system('cls' if os.name == 'nt' else 'clear')

def print_game_state(state):
    """Função de "renderização" do terminal."""
    global IS_MY_TURN, GAME_OVER
    
    with STATE_LOCK:
        clear_screen()
        print("================ BUCKSHOT ROULETTE (gRPC) ================")
        print(f"Sala: {state.room_id} | Status: {state.status}")
        print(f"Log: {state.last_action_log}\n")
        
        # Pente
        print(f"--- PENTE ATUAL ---")
        print(f"Balas Totais: {state.bullets_in_clip}")
        print(f"Balas Reais:  {state.live_bullets_in_clip}")
        print(f"Balas Festim: {state.bullets_in_clip - state.live_bullets_in_clip}\n")
        
        # Jogadores
        print("--- JOGADORES ---")
        print(f"  {state.player1_name}: {state.player1_lives} vidas")
        if state.player2_name:
            print(f"  {state.player2_name}: {state.player2_lives} vidas")
        print("-------------------")

        # Turno / Fim de Jogo
        if state.status == "GAME_OVER":
            print(f"\n!!!!!!!! FIM DE JOGO !!!!!!!!")
            print(f"{state.winner_id} VENCEU!")
            print("==========================================================")
            IS_MY_TURN = False
            GAME_OVER = True
        elif state.status == "IN_GAME":
            if state.current_turn_player_id == PLAYER_NAME:
                print(f"\n>>>> É A SUA VEZ, {PLAYER_NAME}! <<<<")
                IS_MY_TURN = True
            else:
                print(f"\n>>>> Esperando a jogada de {state.current_turn_player_id}... <<<<")
                IS_MY_TURN = False
        elif state.status == "WAITING":
            print("\n>>>> Esperando outro jogador entrar na sala... <<<<")
            IS_MY_TURN = False

def listen_for_updates(stub):
    """Thread dedicada a escutar o stream de GameState do servidor."""
    global CURRENT_GAME_STATE
    
    print(f"Cliente: Conectando ao stream da sala {CURRENT_ROOM_ID} como {PLAYER_NAME}...")
    try:
        request = game_pb2.SubscribeRequest(room_id=CURRENT_ROOM_ID, player_id=PLAYER_NAME)
        stream = stub.SubscribeToGameUpdates(request)
        
        for state in stream:
            # A cada novo estado recebido, atualiza a variável global e imprime
            with STATE_LOCK:
                CURRENT_GAME_STATE = state
            print_game_state(state)
            
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.CANCELLED:
            print("Stream cancelado pelo cliente.")
        else:
            print(f"Erro no stream gRPC: {e.details()} (Código: {e.code()})")
    except Exception as e:
        print(f"Erro na thread de escuta: {e}")
    finally:
        print("Thread de escuta terminada.")
        # Se a thread morrer, sinaliza o fim do jogo
        global GAME_OVER
        GAME_OVER = True 

def game_input_loop(stub):
    """Thread dedicada a capturar o input do usuário para jogar."""
    global IS_MY_TURN, GAME_OVER, CURRENT_GAME_STATE
    
    while not GAME_OVER:
        # Pega o status atual de forma segura
        current_status = ""
        with STATE_LOCK:
            if CURRENT_GAME_STATE:
                current_status = CURRENT_GAME_STATE.status

        # --- Lógica de sair da espera ---
        if current_status == "WAITING":
            print("\nVocê está esperando um oponente.")
            print("  Digite 'sair' para voltar ao lobby.")
            choice = input("Ação: ").strip().lower()

            # Checa o estado DE NOVO, *depois* do input.
            # O estado pode ter mudado enquanto o usuário digitava.
            with STATE_LOCK:
                new_status = ""
                if CURRENT_GAME_STATE:
                    new_status = CURRENT_GAME_STATE.status
            
            # Se o estado mudou (ex: de WAITING para IN_GAME),
            # ignora o input ('choice') e recomeça o loop principal.
            if new_status != "WAITING":
                continue

            if choice == 'sair':
                try:
                    # Envia uma jogada de "desistência"
                    request = game_pb2.MoveRequest(
                        room_id=CURRENT_ROOM_ID,
                        player_id=PLAYER_NAME,
                        action=game_pb2.QUIT_GAME
                    )
                    stub.MakeMove(request)
                    # O servidor vai processar, fechar o jogo, e o
                    # stream 'listen_for_updates' vai nos desconectar.
                    GAME_OVER = True # Força a saída do loop local
                    break # Sai do game_input_loop
                except grpc.RpcError as e:
                    print(f"Erro ao sair da sala: {e.details()}")
            else:
                print("Opção inválida.")
        
        # --- LÓGICA DE JOGO ORIGINAL ---
        elif IS_MY_TURN:
            print("\nEscolha sua ação:")
            print("  1: Atirar no Oponente")
            print("  2: Atirar em Si Mesmo")
            print("  3: Desistir")
            choice = input("Ação (1-3): ")

            action = None
            if choice == '1':
                action = game_pb2.SHOOT_OPPONENT
            elif choice == '2':
                action = game_pb2.SHOOT_SELF
            elif choice == '3':
                action = game_pb2.QUIT_GAME
            else:
                print("Escolha inválida.")
                continue # Pergunta de novo

            # Envia a jogada (RPC)
            try:
                request = game_pb2.MoveRequest(
                    room_id=CURRENT_ROOM_ID,
                    player_id=PLAYER_NAME,
                    action=action
                )
                stub.MakeMove(request)
                # O estado não é atualizado aqui, mas sim pelo stream
                IS_MY_TURN = False # Previne jogada dupla
                
                if action == game_pb2.QUIT_GAME:
                    GAME_OVER = True
                    
            except grpc.RpcError as e:
                # Mostra o erro (ex: "Não é seu turno")
                print(f"Erro ao fazer jogada: {e.details()}")
        
        # Dorme um pouco para não fritar a CPU
        time.sleep(0.1)


def main_menu(stub):
    """Menu principal (Lobby)."""
    global PLAYER_NAME, CURRENT_ROOM_ID, GAME_OVER, IS_MY_TURN

    while True:
        clear_screen()
        print(f"Bem-vindo, {PLAYER_NAME}! (Servidor: localhost:50051)")
        print("\n--- LOBBY PRINCIPAL ---")
        print("  1: Ver salas disponíveis")
        print("  2: Criar uma sala")
        print("  3: Entrar em uma sala")
        print("  4: Sair")
        choice = input("Escolha uma opção: ")

        if choice == '1':
            # --- Listar Lobbies ---
            try:
                response = stub.GetLobbies(game_pb2.Empty())
                print("\n--- Salas Ativas ---")
                if not response.rooms:
                    print("Nenhuma sala encontrada.")
                for room in response.rooms:
                    print(f"  - ID: {room.room_id} | Nome: {room.room_name} | Jogadores: {room.player_count}/2 | Status: {room.status}")
                input("\nPressione Enter para continuar...")
            except grpc.RpcError as e:
                print(f"Erro ao listar salas: {e.details()}")
                time.sleep(2)

        elif choice == '2':
            # --- Criar Sala ---
            room_name = input("Digite o nome da sua sala: ")
            try:
                request = game_pb2.CreateRoomRequest(player_name=PLAYER_NAME, room_name=room_name)
                response = stub.CreateRoom(request)
                CURRENT_ROOM_ID = response.room_id
                print(f"Sala '{response.room_name}' (ID: {response.room_id}) criada! Esperando oponente...")
                
                # Entra no loop do jogo
                start_game_threads(stub)
                
            except grpc.RpcError as e:
                print(f"Erro ao criar sala: {e.details()}")
                time.sleep(2)

        elif choice == '3':
            # --- Entrar na Sala ---
            room_id = input("Digite o ID da sala para entrar: ")
            try:
                request = game_pb2.JoinRoomRequest(player_name=PLAYER_NAME, room_id=room_id)
                response = stub.JoinRoom(request)
                CURRENT_ROOM_ID = response.room_id
                print(f"Você entrou na sala '{response.room_name}'!")
                
                # Entra no loop do jogo
                start_game_threads(stub)

            except grpc.RpcError as e:
                print(f"Erro ao entrar na sala: {e.details()}")
                time.sleep(2)
        
        elif choice == '4':
            print("Até logo!")
            break
        else:
            print("Opção inválida.")
            time.sleep(1)

def start_game_threads(stub):
    """Inicia as duas threads para o jogo (escuta e input)."""
    global GAME_OVER, IS_MY_TURN
    # Reseta o estado do jogo
    GAME_OVER = False
    IS_MY_TURN = False

    # Thread 1: Escuta o servidor
    listener_thread = threading.Thread(target=listen_for_updates, args=(stub,), daemon=True)
    listener_thread.start()

    # Thread 2: Captura input (esta é a thread principal do jogo)
    game_input_loop(stub)
    
    # Quando o jogo acabar (loop de input terminar)
    # A thread de escuta (listen_for_updates) já imprimiu a mensagem final
    # de GAME_OVER. Agora, apenas esperamos o usuário confirmar.
    print("\n==========================================================")
    input("Pressione Enter para voltar ao lobby...")
    
    # Tenta limpar a thread de escuta (que já deve ter terminado)
    listener_thread.join(timeout=1)


def run():
    global PLAYER_NAME
    while not PLAYER_NAME:
        PLAYER_NAME = input("Digite seu nome de jogador: ")

    # Altere 'localhost' para o IP do servidor se estiver em máquinas diferentes
    server_address = 'localhost:50051'
    
    with grpc.insecure_channel(server_address) as channel:
        stub = game_pb2_grpc.GameServerStub(channel)
        print(f"Conectado ao servidor em {server_address}")
        time.sleep(1)
        main_menu(stub)

if __name__ == '__main__':
    run()