import React from 'react';
import {
  Eye,
  Volume2,
  Settings,
  Mic,
  MicOff,
  Send,
  User,
  Bot,
  EyeIcon,
  Zap,
  Camera,
  Brain,
  Square,
  RefreshCw,
  Phone,
  PhoneOff,
  Wind,
  Thermometer,
  Sparkles,
  Plus,
  History,
  Search,
  Pencil,
  Trash2,
  ChevronLeft,
  ChevronRight,
  ChefHat,
} from 'lucide-react';

export type CustomIconName = 'logo' | 'eye' | 'speaker' | 'gear' | 'mic' | 'mic-off' | 'send' | 'user' | 'bot' | 'vision' | 'zap' | 'camera' | 'brain' | 'stop' | 'refresh-cw' | 'phone' | 'phone-off' | 'wind' | 'thermometer' | 'plus' | 'history' | 'search' | 'pencil' | 'trash' | 'chevron-left' | 'chevron-right' | 'chef-hat' | 'sparkles';

interface CustomIconProps {
  name: CustomIconName;
  size?: number;
  color?: string;
  className?: string;
}

// 图标映射表
const iconMap = {
  logo: Sparkles,
  eye: Eye,
  speaker: Volume2,
  gear: Settings,
  mic: Mic,
  'mic-off': MicOff,
  send: Send,
  user: User,
  bot: Bot,
  vision: EyeIcon,
  zap: Zap,
  camera: Camera,
  brain: Brain,
  stop: Square,
  'refresh-cw': RefreshCw,
  phone: Phone,
  'phone-off': PhoneOff,
  wind: Wind,
  thermometer: Thermometer,
  plus: Plus,
  history: History,
  search: Search,
  pencil: Pencil,
  trash: Trash2,
  'chevron-left': ChevronLeft,
  'chevron-right': ChevronRight,
  'chef-hat': ChefHat,
  sparkles: Sparkles,
};

export const CustomIcon: React.FC<CustomIconProps> = ({ 
  name, 
  size = 24, 
  color = 'currentColor', 
  className = '' 
}) => {
  const IconComponent = iconMap[name];
  
  if (!IconComponent) {
    console.warn(`Icon "${name}" not found`);
    return null;
  }
  
  return (
    <IconComponent 
      size={size} 
      color={color} 
      className={className}
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  );
};
