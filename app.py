from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from googletrans import Translator
import uuid
import logging
import os
import time
import threading

# 로깅 설정
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here')
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

# 번역기 초기화
translator = Translator()

# 연결된 사용자들 정보 저장
users = {}

# 사용자 연결 상태 추적 (heartbeat 기반)
user_heartbeats = {}

# 언어 코드 매핑
LANGUAGES = {
    'korean': 'ko',
    'english': 'en', 
    'japanese': 'ja',
    'traditional_chinese': 'zh-tw'
}

LANGUAGE_NAMES = {
    'ko': '한국어',
    'en': 'English',
    'ja': '日本語',
    'zh-tw': '繁體中文'
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

def broadcast_user_list_update():
    """모든 사용자에게 업데이트된 사용자 목록을 전송"""
    current_users = []
    for sid, user_info in users.items():
        if user_info['nickname'] and user_info.get('active', True):  # 활성 사용자만
            current_users.append({
                'nickname': user_info['nickname'],
                'language': LANGUAGE_NAMES.get(user_info['language'], user_info['language'])
            })
    
    # 모든 사용자에게 업데이트된 목록 전송
    for sid in users:
        emit('user_list_update', {'users': current_users}, room=sid)

def check_inactive_users():
    """비활성 사용자들을 정리하는 함수"""
    current_time = time.time()
    inactive_users = []
    
    for sid, last_heartbeat in user_heartbeats.items():
        # 30초 이상 heartbeat가 없으면 비활성으로 간주
        if current_time - last_heartbeat > 30:
            inactive_users.append(sid)
    
    for sid in inactive_users:
        if sid in users:
            user = users[sid]
            if user.get('nickname'):  # 닉네임이 설정된 사용자만
                # 다른 사용자들에게 퇴장 알림
                for other_sid, other_user in users.items():
                    if other_sid != sid and other_user.get('nickname'):
                        target_lang = other_user['language']
                        leave_msg = translate_text(f"{user['nickname']}님이 연결이 끊어졌습니다.", target_lang)
                        emit('user_left', {
                            'message': leave_msg,
                            'nickname': user['nickname'],
                            'user': {
                                'nickname': user['nickname'],
                                'language': user['language']
                            }
                        }, room=other_sid)
            
            # 사용자 정보 삭제
            del users[sid]
            if sid in user_heartbeats:
                del user_heartbeats[sid]
    
    # 사용자 목록 업데이트
    if inactive_users:
        broadcast_user_list_update()

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
        'active': True,
        'connected_at': time.time()
    }
    user_heartbeats[request.sid] = time.time()
    print(f'사용자 연결됨: {request.sid}')

@socketio.on('disconnect')
def on_disconnect():
    if request.sid in users:
        user = users[request.sid]
        # 실제로 닉네임이 설정된 사용자만 퇴장 처리
        if user.get('nickname'):
            # 다른 사용자들에게 퇴장 알림
            for sid, other_user in users.items():
                if sid != request.sid and other_user.get('nickname'):
                    target_lang = other_user['language']
                    leave_msg = translate_text(f"{user['nickname']}님이 채팅방을 나갔습니다.", target_lang)
                    emit('user_left', {
                        'message': leave_msg,
                        'nickname': user['nickname'],
                        'user': {
                            'nickname': user['nickname'],
                            'language': user['language']
                        }
                    }, room=sid)
        
        # 사용자 정보 삭제
        del users[request.sid]
        if request.sid in user_heartbeats:
            del user_heartbeats[request.sid]
        
        # 사용자 목록 업데이트
        broadcast_user_list_update()
        print(f'사용자 연결 해제됨: {request.sid}')

@socketio.on('heartbeat')
def on_heartbeat():
    """사용자 heartbeat 처리"""
    if request.sid in users:
        user_heartbeats[request.sid] = time.time()
        users[request.sid]['active'] = True
        emit('heartbeat_ack', {'timestamp': time.time()})

@socketio.on('request_user_list')
def on_request_user_list():
    """사용자 목록 요청 처리"""
    if request.sid in users:
        current_users = []
        for sid, user_info in users.items():
            if user_info['nickname'] and user_info.get('active', True):  # 활성 사용자만
                current_users.append({
                    'nickname': user_info['nickname'],
                    'language': LANGUAGE_NAMES.get(user_info['language'], user_info['language'])
                })
        
        emit('user_list_update', {'users': current_users})

@socketio.on('set_user_info')
def on_set_user_info(data):
    print(f"사용자 정보 설정 받음: {data}")
    if request.sid in users:
        users[request.sid]['nickname'] = data['nickname']
        users[request.sid]['language'] = LANGUAGES.get(data['language'], 'ko')
        users[request.sid]['active'] = True
        user_heartbeats[request.sid] = time.time()
        
        print(f"사용자 정보 업데이트됨: {users[request.sid]}")
        
        # 다른 사용자들에게 입장 알림
        for sid, other_user in users.items():
            if sid != request.sid and other_user.get('nickname'):
                target_lang = other_user['language']
                join_msg = translate_text(f"{data['nickname']}님이 채팅방에 입장했습니다.", target_lang)
                emit('user_joined', {
                    'message': join_msg,
                    'nickname': data['nickname'],
                    'user': {
                        'nickname': data['nickname'],
                        'language': data['language']
                    }
                }, room=sid)
        
        emit('user_info_set', {'success': True})
        
        # 사용자 목록 업데이트
        broadcast_user_list_update()
        print(f"user_info_set 이벤트 전송됨")

@socketio.on('send_message')
def on_send_message(data):
    if request.sid not in users:
        return
    
    sender = users[request.sid]
    if not sender.get('nickname'):  # 닉네임이 설정되지 않은 사용자는 메시지 전송 불가
        return
        
    original_message = data['message']
    sender_nickname = sender['nickname']
    sender_lang = sender['language']
    
    # heartbeat 업데이트
    user_heartbeats[request.sid] = time.time()
    users[request.sid]['active'] = True
    
    # 모든 연결된 사용자에게 번역된 메시지 전송
    for sid, user in users.items():
        if not user.get('nickname'):  # 닉네임이 설정되지 않은 사용자에게는 전송하지 않음
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

def cleanup_inactive_users():
    """백그라운드에서 비활성 사용자 정리"""
    while True:
        try:
            time.sleep(15)  # 15초마다 체크
            with app.app_context():
                check_inactive_users()
        except Exception as e:
            print(f"Cleanup 오류: {e}")
            time.sleep(5)  # 오류 발생 시 5초 후 재시도

# 앱 시작 시 cleanup 작업 시작
def start_cleanup_on_startup():
    """앱 시작 시 cleanup 작업 시작"""
    def run_cleanup():
        cleanup_inactive_users()
    
    cleanup_thread = threading.Thread(target=run_cleanup, daemon=True)
    cleanup_thread.start()

if __name__ == '__main__':
    # 앱 시작 시 cleanup 작업 시작
    start_cleanup_on_startup()
    
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, debug=False, host='0.0.0.0', port=port)