import OpenAI from 'openai'

export function createOpenAIClient() {
  return new OpenAI({
    baseURL: 'https://openrouter.ai/api/v1',
    apiKey:  process.env.OPENROUTER_API_KEY ?? '',
  })
}

export const OPENROUTER_MODEL = process.env.OPENROUTER_MODEL ?? 'qwen/qwen3-235b-a22b'
