from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from googletrans import Translator
import uuid
import logging
import time
from datetime import datetime

# 로깅 설정
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

# 번역기 초기화
translator = Translator()

# 연결된 사용자들 정보 저장 (sid -> user_info)
users = {}
# 사용자 ID로 sid 찾기 (user_id -> sid)
user_sid_map = {}
# 마지막 heartbeat 시간 추적
last_heartbeat = {}

# 언어 코드 매핑
LANGUAGES = {
    'korean': 'ko',
    'english': 'en', 
    'japanese': 'ja'
}

LANGUAGE_NAMES = {
    'ko': '한국어',
    'en': 'English',
    'ja': '日本語'
}

def translate_text(text, target_lang):
    """텍스트를 목표 언어로 번역"""
    try:
        if target_lang == 'auto':
            return text
        result = translator.translate(text, dest=target_lang)
        return result.text
    except Exception as e:
        print(f"번역 오류: {e}")
        return text

def broadcast_user_list():
    """모든 사용자에게 현재 접속 인원 목록 전송"""
    user_list = []
    for sid, user in users.items():
        if user['nickname']:  # 닉네임이 설정된 사용자만
            user_list.append({
                'nickname': user['nickname'],
                'language': LANGUAGE_NAMES.get(user['language'], user['language']),
                'user_id': user['user_id']
            })
    
    # 모든 사용자에게 사용자 목록 전송
    for sid in users:
        emit('user_list_update', {'users': user_list}, room=sid)

def remove_user(sid):
    """사용자 제거 및 정리"""
    if sid in users:
        user = users[sid]
        user_id = user['user_id']
        
        # user_sid_map에서 제거
        if user_id in user_sid_map:
            del user_sid_map[user_id]
        
        # users에서 제거
        del users[sid]
        
        # 마지막 heartbeat 제거
        if sid in last_heartbeat:
            del last_heartbeat[sid]
        
        print(f'사용자 제거됨: {sid} - {user.get("nickname", "Unknown")}')

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def on_connect():
    user_id = str(uuid.uuid4())
    users[request.sid] = {
        'user_id': user_id,
        'nickname': '',
        'language': 'ko',
        'connected_at': datetime.now().isoformat()
    }
    user_sid_map[user_id] = request.sid
    last_heartbeat[request.sid] = time.time()
    
    print(f'사용자 연결됨: {request.sid} (ID: {user_id})')
    
    # 현재 사용자 목록 전송
    broadcast_user_list()

@socketio.on('disconnect')
def on_disconnect():
    if request.sid in users:
        user = users[request.sid]
        nickname = user.get('nickname', '')
        
        # 닉네임이 설정된 사용자만 퇴장 알림
        if nickname:
            for sid, other_user in users.items():
                if sid != request.sid and other_user.get('nickname'):
                    target_lang = other_user['language']
                    leave_msg = translate_text(f"{nickname}님이 채팅방을 나갔습니다.", target_lang)
                    emit('user_left', {
                        'message': leave_msg,
                        'nickname': nickname
                    }, room=sid)
        
        remove_user(request.sid)
        broadcast_user_list()
        print(f'사용자 연결 해제됨: {request.sid}')

@socketio.on('heartbeat')
def on_heartbeat():
    """클라이언트 heartbeat 응답"""
    if request.sid in users:
        last_heartbeat[request.sid] = time.time()
        emit('heartbeat_ack', {'timestamp': time.time()})

@socketio.on('set_user_info')
def on_set_user_info(data):
    print(f"사용자 정보 설정 받음: {data}")
    if request.sid in users:
        users[request.sid]['nickname'] = data['nickname']
        users[request.sid]['language'] = LANGUAGES.get(data['language'], 'ko')
        
        print(f"사용자 정보 업데이트됨: {users[request.sid]}")
        
        # 다른 사용자들에게 입장 알림
        for sid, other_user in users.items():
            if sid != request.sid and other_user.get('nickname'):
                target_lang = other_user['language']
                join_msg = translate_text(f"{data['nickname']}님이 채팅방에 입장했습니다.", target_lang)
                emit('user_joined', {
                    'message': join_msg,
                    'nickname': data['nickname']
                }, room=sid)
        
        emit('user_info_set', {'success': True})
        broadcast_user_list()
        print(f"user_info_set 이벤트 전송됨")

@socketio.on('send_message')
def on_send_message(data):
    if request.sid not in users:
        return
    
    sender = users[request.sid]
    if not sender.get('nickname'):
        return
    
    original_message = data['message']
    sender_nickname = sender['nickname']
    sender_lang = sender['language']
    
    # 모든 연결된 사용자에게 번역된 메시지 전송
    for sid, user in users.items():
        if not user.get('nickname'):
            continue
            
        target_lang = user['language']
        
        if sid == request.sid:
            # 발신자에게는 원본 메시지
            translated_message = original_message
        else:
            # 다른 사용자에게는 번역된 메시지
            translated_message = translate_text(original_message, target_lang)
        
        emit('receive_message', {
            'nickname': sender_nickname,
            'message': translated_message,
            'original_language': LANGUAGE_NAMES.get(sender_lang, sender_lang),
            'is_own_message': (sid == request.sid)
        }, room=sid)

# 주기적으로 연결 상태 확인
def check_connections():
    """주기적으로 연결 상태를 확인하고 비정상 연결 정리"""
    current_time = time.time()
    disconnected_sids = []
    
    for sid, last_time in last_heartbeat.items():
        if current_time - last_time > 30:  # 30초 이상 heartbeat가 없으면
            disconnected_sids.append(sid)
    
    for sid in disconnected_sids:
        if sid in users:
            print(f"비정상 연결 감지 및 정리: {sid}")
            remove_user(sid)
    
    if disconnected_sids:
        broadcast_user_list()

# 백그라운드 작업 시작
import threading
def background_task():
    while True:
        time.sleep(10)  # 10초마다 체크
        check_connections()

threading.Thread(target=background_task, daemon=True).start()

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)