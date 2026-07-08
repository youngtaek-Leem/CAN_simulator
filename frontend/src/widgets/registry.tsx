import type { ComponentType } from 'react';
import type { WidgetConfig, WidgetType } from '../types';
import { CanMessageDisplay, TextDisplay } from './displays';
import { ButtonWidget, CheckboxWidget, DropdownWidget, SliderWidget } from './controls';
import { MultiButtonWidget, MultiCheckboxWidget } from './MultiControls';
import { TxBox } from './TxBox';
import { ReplayBox } from './ReplayBox';
import { IsoTpBox } from './IsoTpBox';

interface WidgetMeta {
  label: string;
  component: ComponentType<{ config: WidgetConfig }>;
  defaultSize: { w: number; h: number; minW: number; minH: number };
}

export const WIDGET_REGISTRY: Record<WidgetType, WidgetMeta> = {
  canMessageDisplay: {
    label: 'CAN 메시지 표시창',
    component: CanMessageDisplay,
    defaultSize: { w: 6, h: 5, minW: 3, minH: 2 },
  },
  textDisplay: {
    label: '텍스트 표시창',
    component: TextDisplay,
    defaultSize: { w: 3, h: 2, minW: 2, minH: 1 },
  },
  button: {
    label: '버튼',
    component: ButtonWidget,
    defaultSize: { w: 2, h: 2, minW: 1, minH: 1 },
  },
  checkbox: {
    label: '체크박스',
    component: CheckboxWidget,
    defaultSize: { w: 2, h: 1, minW: 1, minH: 1 },
  },
  dropdown: {
    label: '드롭다운',
    component: DropdownWidget,
    defaultSize: { w: 3, h: 1, minW: 2, minH: 1 },
  },
  slider: {
    label: '슬라이더',
    component: SliderWidget,
    defaultSize: { w: 4, h: 2, minW: 2, minH: 1 },
  },
  txBox: {
    label: 'CAN 메시지 전송 박스',
    component: TxBox,
    defaultSize: { w: 7, h: 5, minW: 4, minH: 3 },
  },
  replayBox: {
    label: 'CAN 로그 Replay 박스',
    component: ReplayBox,
    defaultSize: { w: 5, h: 3, minW: 3, minH: 2 },
  },
  multiButton: {
    label: '멀티 버튼',
    component: MultiButtonWidget,
    defaultSize: { w: 6, h: 4, minW: 1, minH: 2 },
  },
  multiCheckbox: {
    label: '멀티 체크박스',
    component: MultiCheckboxWidget,
    defaultSize: { w: 6, h: 4, minW: 1, minH: 2 },
  },
  isotpTx: {
    label: 'ISO-TP 메시지 전송',
    component: IsoTpBox,
    defaultSize: { w: 6, h: 4, minW: 1, minH: 2 },
  },
};
