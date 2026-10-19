export {};

declare global {
  interface Window {
    electronAPI?: {
      getAppVersion: () => Promise<string>;
      openExternal: (url: string) => Promise<void>;
      showMessageBox: (options: {
        type?: 'none' | 'info' | 'error' | 'question' | 'warning';
        title?: string;
        message: string;
        detail?: string;
        buttons?: string[];
      }) => Promise<{ response: number }>;
      platform: string;
      isElectron: boolean;
    };
  }
}
