import grpc
from concurrent import futures
import time
import threading
import random
import uuid
import queue

# Importa as classes geradas
import game_pb2
import game_pb2_grpc

INITIAL_LIVES = 3

# Classe interna do servidor para gerenciar o estado de UM jogo
class GameRoom:
    def __init__(self, room_name, host_name):
        self.room_id = f"room-{uuid.uuid4().hex[:6]}"
        self.room_name = room_name
        self.players = {} # Dicionário de player_id -> { "name": str, "lives": int }
        self.host_id = host_name
        self.status = "WAITING"
        self.clip = [] # Lista de booleanos: True = Real, False = Festim
        self.current_turn_player_id = None
        self.last_action_log = "Jogo criado. Esperando oponente..."
        self.winner_id = None
        
        # Lista de "observadores" (clientes) para enviar updates (o stream do gRPC)
        self.subscriber_queues = [] 
        
        # Lock individual para este jogo, essencial para concorrência
        self.lock = threading.RLock() 
        
        # Adiciona o host
        self.add_player(host_name)

    def add_player(self, player_name):
        # A lógica do jogo só aceita 2 jogadores
        if len(self.players) >= 2:
            raise Exception("Sala está cheia")
        
        player_id = player_name # Usando o nome como ID por simplicidade
        self.players[player_id] = {"name": player_name, "lives": INITIAL_LIVES}
        
        if len(self.players) == 2:
            self.start_game()
        else:
            self.last_action_log = f"{player_name} entrou na sala. Esperando oponente..."
            self._broadcast_state()
        
        return player_id

    def start_game(self):
        self.status = "IN_GAME"
        player_ids = list(self.players.keys())
        self.current_turn_player_id = random.choice(player_ids)
        self._load_clip()
        self.last_action_log = f"Jogo iniciado! {len(self.clip)} balas no pente ({self.clip.count(True)} reais). Vez de {self.current_turn_player_id}."
        self._broadcast_state()

    def _load_clip(self):
        # Lógica simples: 2 a 8 balas, ~metade real
        total = random.randint(2, 8)
        live = total // 2
        if total % 2 != 0 and random.choice([True, False]): # Aleatoriedade extra
             live += 1
             
        self.clip = [True] * live + [False] * (total - live)
        random.shuffle(self.clip)
        print(f"Sala {self.room_id}: Carregando pente. {total} balas, {live} reais. Pente: {self.clip}")

    def get_opponent_id(self, player_id):
        for pid in self.players.keys():
            if pid != player_id:
                return pid
        return None

    def make_move(self, player_id, action):
        with self.lock:
            # --- LÓGICA DE AÇÃO DO JOGO ---
            # 1. Checa por desistência PRIMEIRO.
            #    Isso é permitido a qualquer momento.
            if action == game_pb2.QUIT_GAME:
                self.players[player_id]["lives"] = 0
                self.last_action_log = f"{player_id} desistiu."
                
                # Se o jogo nem começou, apenas define o vencedor como "Ninguém"
                # ou o outro jogador, se ele existir
                if self.status == "WAITING":
                    self.status = "GAME_OVER"
                    opponent = self.get_opponent_id(player_id)
                    self.winner_id = opponent if opponent else "Ninguém"
                    
                # A checagem de vitória normal (abaixo)
                # vai cuidar da lógica se o jogo estava IN_GAME.

            # 2. Se não for desistência, faz as validações normais.
            elif self.status != "IN_GAME":
                raise Exception("Jogo não está ativo")
            elif player_id != self.current_turn_player_id:
                raise Exception("Não é o seu turno")

            # 3. Executa a ação (apenas se não for desistência)
            elif not self.clip: # 'elif' é importante aqui
                self._load_clip()
                self.last_action_log = f"Pente vazio. Recarregando! {len(self.clip)} balas ({self.clip.count(True)} reais)."
                # O turno continua com o mesmo jogador
            
            elif action == game_pb2.SHOOT_SELF:
                if not self.clip: # Checagem dupla por segurança
                     self.last_action_log = "Pente vazio, mas _load_clip falhou?" # Log de segurança
                     self._broadcast_state()
                     return

                is_live = self.clip.pop(0) # Pega a próxima bala
                if is_live:
                    self.players[player_id]["lives"] -= 1
                    self.last_action_log = f"{player_id} atirou em si mesmo... ERA REAL! -1 vida. A vez passa."
                    self.current_turn_player_id = self.get_opponent_id(player_id)
                else:
                    self.last_action_log = f"{player_id} atirou em si mesmo... FESTIM! A vez continua."
                    # O turno não muda
                    
            elif action == game_pb2.SHOOT_OPPONENT:
                if not self.clip: # Checagem dupla por segurança
                     self.last_action_log = "Pente vazio, mas _load_clip falhou?" # Log de segurança
                     self._broadcast_state()
                     return
                     
                opponent_id = self.get_opponent_id(player_id)
                is_live = self.clip.pop(0) # Pega a próxima bala
                if is_live:
                    self.players[opponent_id]["lives"] -= 1
                    self.last_action_log = f"{player_id} atirou em {opponent_id}... ERA REAL! {opponent_id} perdeu 1 vida."
                else:
                    self.last_action_log = f"{player_id} atirou em {opponent_id}... FESTIM! Ninguém se feriu."
                
                # A vez sempre passa
                self.current_turn_player_id = opponent_id
            
            # --- FIM DA LÓGICA DE AÇÃO ---

            # 4. Checa condição de vitória
            # Adiciona 'and self.status != "GAME_OVER"' para evitar definir o vencedor duas vezes
            for pid, data in self.players.items():
                if data["lives"] <= 0 and self.status != "GAME_OVER":
                    self.winner_id = self.get_opponent_id(pid)
                    if not self.winner_id: # Se não achou oponente (ex: desistiu no lobby)
                        self.winner_id = "Ninguém"
                        
                    self.status = "GAME_OVER"
                    self.last_action_log += f" FIM DE JOGO! {self.winner_id} venceu!"
                    break # Encontrou um perdedor, pode parar

            # 5. Notifica todos os clientes
            self._broadcast_state()

    def _broadcast_state(self):
        # Envia o estado atual para TODOS os observadores
        state_proto = self._get_state_proto()
        print(f"Sala {self.room_id}: Transmitindo estado -> {self.last_action_log}")
        
        with self.lock:
            # Itera sobre uma cópia, caso a lista seja modificada
            for q in list(self.subscriber_queues):
                try:
                    q.put(state_proto) # Coloca o estado na fila do cliente
                except Exception as e:
                    print(f"Erro ao colocar na fila: {e}, removendo fila.")
                    # Se der erro (ex: cliente desconectou), remove a fila
                    if q in self.subscriber_queues:
                        self.subscriber_queues.remove(q)

    def _get_state_proto(self):
        # Converte o estado interno da classe para a mensagem gRPC
        pids = list(self.players.keys())
        p1_name = ""
        p1_lives = 0
        p2_name = ""
        p2_lives = 0

        if len(pids) > 0:
            p1_name = self.players[pids[0]]["name"]
            p1_lives = self.players[pids[0]]["lives"]
        if len(pids) > 1:
            p2_name = self.players[pids[1]]["name"]
            p2_lives = self.players[pids[1]]["lives"]

        return game_pb2.GameState(
            room_id=self.room_id,
            status=self.status,
            player1_name=p1_name,
            player2_name=p2_name,
            player1_lives=p1_lives,
            player2_lives=p2_lives,
            current_turn_player_id=self.current_turn_player_id or "",
            bullets_in_clip=len(self.clip),
            live_bullets_in_clip=self.clip.count(True),
            last_action_log=self.last_action_log,
            winner_id=self.winner_id or "",
        )
        
# --- Implementação do Servidor gRPC ---

# O "Gerenciador de Salas" global
ROOMS = {} # Dicionário de room_id -> GameRoom
ROOMS_LOCK = threading.Lock() # Lock para proteger o dicionário ROOMS

class GameServerImpl(game_pb2_grpc.GameServerServicer):

    def GetLobbies(self, request, context):
        lobbies = []
        with ROOMS_LOCK:
            for room in ROOMS.values():
                lobbies.append(game_pb2.RoomInfo(
                    room_id=room.room_id,
                    room_name=room.room_name,
                    player_count=len(room.players),
                    status=room.status
                ))
        return game_pb2.LobbyList(rooms=lobbies)

    def CreateRoom(self, request, context):
        try:
            room = GameRoom(request.room_name, request.player_name)
            with ROOMS_LOCK:
                ROOMS[room.room_id] = room
            
            print(f"Sala criada: {room.room_name} ({room.room_id}) por {request.player_name}")
            return game_pb2.RoomInfo(
                room_id=room.room_id,
                room_name=room.room_name,
                player_count=1,
                status=room.status
            )
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Erro ao criar sala: {e}")
            return game_pb2.RoomInfo()

    def JoinRoom(self, request, context):
        with ROOMS_LOCK:
            room = ROOMS.get(request.room_id)
        
        if not room:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Sala {request.room_id} não encontrada.")
            return game_pb2.RoomInfo()
            
        try:
            with room.lock: # Garante que ninguém mais entre ao mesmo tempo
                room.add_player(request.player_name)
            
            print(f"{request.player_name} entrou na sala {room.room_name}")
            return game_pb2.RoomInfo(
                room_id=room.room_id,
                room_name=room.room_name,
                player_count=len(room.players),
                status=room.status
            )
        except Exception as e:
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details(f"Erro ao entrar na sala: {e}")
            return game_pb2.RoomInfo()

    def MakeMove(self, request, context):
        with ROOMS_LOCK:
            room = ROOMS.get(request.room_id)
            
        if not room:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("Sala não encontrada")
            return game_pb2.MoveResponse(success=False, error_message="Sala não encontrada")

        try:
            room.make_move(request.player_id, request.action)
            return game_pb2.MoveResponse(success=True)
        except Exception as e:
            print(f"Erro na jogada: {e}")
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details(str(e))
            return game_pb2.MoveResponse(success=False, error_message=str(e))

    def SubscribeToGameUpdates(self, request, context):
        with ROOMS_LOCK:
            room = ROOMS.get(request.room_id)

        if not room:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("Sala não encontrada")
            return

        print(f"{request.player_id} se inscreveu para updates da sala {request.room_id}")
        
        # 1. Cria uma fila ÚNICA para este cliente
        update_queue = queue.Queue()
        
        # 2. Adiciona a fila à lista de assinantes da sala
        with room.lock:
            room.subscriber_queues.append(update_queue)

        try:
            # 3. Envia o estado atual imediatamente
            # (O 'add_player' já terá colocado o estado de "espera" na fila)
            # Vamos garantir que ele pegue o estado mais recente
            initial_state = room._get_state_proto()
            update_queue.put(initial_state)

            # 4. Loop principal: espera por atualizações na fila e 'yield' (envia)
            while context.is_active():
                try:
                    # Bloqueia a thread até uma nova atualização chegar (com timeout)
                    state = update_queue.get(timeout=1.0) 
                    
                    yield state # ENVIA O ESTADO PARA O CLIENTE
                    
                    # Se o jogo acabou, para de escutar
                    if state.status == "GAME_OVER":
                        break
                except queue.Empty:
                    # Timeout de 1s, apenas checa se o cliente ainda está ativo
                    continue 
        
        except Exception as e:
            print(f"Erro no stream para {request.player_id}: {e}")
        
        finally:
            # 5. Limpeza: Remove a fila da lista quando o cliente desconectar
            print(f"{request.player_id} desconectou da sala {request.room_id}")
            with room.lock:
                if update_queue in room.subscriber_queues:
                    room.subscriber_queues.remove(update_queue)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    game_pb2_grpc.add_GameServerServicer_to_server(GameServerImpl(), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    print("Servidor gRPC iniciado na porta 50051.")
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("Servidor parando...")
        server.stop(0)

if __name__ == '__main__':
    serve()