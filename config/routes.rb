Rails.application.routes.draw do
  get "home/index"

  namespace :api do
    resources :prompts, only: [ :create, :show ]

    namespace :jobs do
      post "claim", to: "dispatch#claim"
      post ":id/heartbeat", to: "dispatch#heartbeat"
      post ":id/chunk", to: "dispatch#chunk"
      post ":id/result", to: "dispatch#result"
    end
  end

  root "home#index"

  get "up" => "rails/health#show", as: :rails_health_check
end
