declare const RTCMultiConnection: {
  new (): RTCMultiConnectionInstance;
};

interface RTCMultiConnectionInstance {
  socketURL: string;
  socketMessageEvent: string;
  iceServers: RTCIceServer[];
  session: {
    audio: boolean;
    video: boolean;
    data?: boolean;
  };
  sdpConstraints: {
    mandatory: {
      OfferToReceiveAudio: boolean;
      OfferToReceiveVideo: boolean;
    };
  };
  maxParticipantsAllowed: number;
  
  open(roomId: string): void;
  join(roomId: string): void;
  openOrJoin(roomId: string): void;
  leave(): void;
  close(): void;
  closeSocket(): void;
  
  onstream: (event: {
    type: 'local' | 'remote';
    stream: MediaStream;
    mediaElement: HTMLVideoElement;
    userid: string;
  }) => void;
  onstreamended: (event: { stream: MediaStream; userid: string }) => void;
  onopen: (event: { userid: string }) => void;
  onclose: (event: { userid: string }) => void;
  onerror: (error: Error) => void;
  onUserDisconnected: (event: { userid: string }) => void;
  onUserStatusChanged: (event: { userid: string; status: string }) => void;
}

declare const io: {
  connect(url: string, options?: any): Socket;
};

interface Socket {
  on(event: string, callback: (...args: any[]) => void): void;
  emit(event: string, ...args: any[]): void;
  disconnect(): void;
}
