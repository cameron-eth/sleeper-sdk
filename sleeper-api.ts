export interface SleeperUser {
  user_id: string
  username: string
  display_name: string
  avatar: string
}

export interface SleeperLeague {
  league_id: string
  name: string
  status: string
  sport: string
  season: string
  total_rosters: number
  draft_id: string
}

export interface SleeperDraft {
  draft_id: string
  status: string
  type: string
  league_id: string
  season: string
  settings: {
    teams: number
    rounds: number
    pick_timer: number
  }
  draft_order: Record<string, number>
  slot_to_roster_id: Record<string, number>
}

export interface SleeperPick {
  pick_no: number
  player_id: string
  picked_by: string
  roster_id: number
  round: number
  draft_slot: number
  metadata: {
    first_name: string
    last_name: string
    position: string
    team: string
  }
}

export interface SleeperTradedPick {
  season: string
  round: number
  roster_id: number
  previous_owner_id: number
  owner_id: number
}

export interface SleeperPlayer {
  player_id: string
  first_name: string
  last_name: string
  position: string
  team: string
  search_full_name: string
  search_rank: number
  status?: string
  age?: number
  years_exp?: number
}

class SleeperAPI {
  private baseUrl = 'https://api.sleeper.app/v1'

  private async fetch(endpoint: string, retries = 3): Promise<any> {
    for (let attempt = 1; attempt <= retries; attempt++) {
      try {
        const response = await fetch(`${this.baseUrl}${endpoint}`, {
          method: 'GET',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
          },
          // Add timeout
          signal: AbortSignal.timeout(10000), // 10 second timeout
        })

        if (!response.ok) {
          if (response.status === 429) {
            // Rate limited - wait and retry
            if (attempt < retries) {
              await new Promise((resolve) => setTimeout(resolve, 1000 * attempt))
              continue
            }
          }
          throw new Error(`Sleeper API error: ${response.status} ${response.statusText}`)
        }

        return response.json()
      } catch (error) {
        console.error(`Sleeper API attempt ${attempt} failed:`, error)

        if (attempt === retries) {
          if (error instanceof Error) {
            if (error.name === 'AbortError') {
              throw new Error('Sleeper API request timed out. Please try again.')
            }
            if (error.message.includes('Failed to fetch')) {
              throw new Error(
                'Network error connecting to Sleeper API. Please check your internet connection and try again.'
              )
            }
            throw error
          }
          throw new Error('Unknown error occurred while fetching data from Sleeper API')
        }

        // Wait before retrying
        await new Promise((resolve) => setTimeout(resolve, 1000 * attempt))
      }
    }
  }

  async getUser(userId: string): Promise<SleeperUser> {
    return this.fetch(`/user/${userId}`)
  }

  async getUserLeagues(
    userId: string,
    sport: string = 'nfl',
    season: string = '2025'
  ): Promise<SleeperLeague[]> {
    return this.fetch(`/user/${userId}/leagues/${sport}/${season}`)
  }

  async getLeague(leagueId: string): Promise<SleeperLeague> {
    return this.fetch(`/league/${leagueId}`)
  }

  async getLeagueDrafts(leagueId: string): Promise<SleeperDraft[]> {
    return this.fetch(`/league/${leagueId}/drafts`)
  }

  async getLeagueRosters(leagueId: string): Promise<any[]> {
    return this.fetch(`/league/${leagueId}/rosters`)
  }

  async getDraft(draftId: string): Promise<SleeperDraft> {
    return this.fetch(`/draft/${draftId}`)
  }

  async getDraftPicks(draftId: string): Promise<SleeperPick[]> {
    return this.fetch(`/draft/${draftId}/picks`)
  }

  async getDraftTradedPicks(draftId: string): Promise<SleeperTradedPick[]> {
    return this.fetch(`/draft/${draftId}/traded_picks`)
  }

  async getAllPlayers(): Promise<Record<string, SleeperPlayer>> {
    return this.fetch('/players/nfl')
  }

  async getNFLState(): Promise<any> {
    return this.fetch('/state/nfl')
  }
}

export const sleeperAPI = new SleeperAPI()
